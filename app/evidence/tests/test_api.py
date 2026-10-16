from datetime import date, timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from common.testing import World
from evidence import refresh
from evidence.models import EnergyRecord, Observation
from evidence.tests.fixtures import EvidenceWorld, make_source


class ObservationValidationTests(TestCase):
    def setUp(self):
        self.w = EvidenceWorld()
        self.vid = str(self.w.vehicle.pk)
        self.now = timezone.now().isoformat()

    def test_wrong_unit_rejected_not_converted(self):
        r = self.w.observe('odometer_km', 1000, 'mi', expect=400,
                           observed_at=self.now, vehicle_id=self.vid)
        self.assertIn('unit', r)
        self.assertIn('does not convert', str(r['unit']))
        self.w.observe('fuel_price_per_l', 95, 'USD/L', expect=400,
                       effective_from='2026-01-01', qualifiers={'fuel':
                                                                'diesel'})

    def test_impossible_values_rejected(self):
        self.w.observe('battery_soc_pct', 140, '%', expect=400,
                       observed_at=self.now, vehicle_id=self.vid)
        self.w.observe('odometer_km', -5, 'km', expect=400,
                       observed_at=self.now, vehicle_id=self.vid)
        self.w.org_value('charging_efficiency_fraction', 1.3, 'fraction',
                         origin='organization_provided', expect=400)

    def test_soc_and_soh_are_distinct_kinds(self):
        # SoH needs the measurement method; SoC does not accept one.
        self.w.observe('battery_soh_pct', 92, '%', expect=400,
                       observed_at=self.now, vehicle_id=self.vid)
        self.w.observe('battery_soc_pct', 80, '%', expect=400,
                       observed_at=self.now, vehicle_id=self.vid,
                       qualifiers={'method': 'bms_reported'})
        soh = self.w.observe('battery_soh_pct', 92, '%',
                             observed_at=self.now, vehicle_id=self.vid,
                             qualifiers={'method': 'bms_reported'})
        soc = self.w.observe('battery_soc_pct', 80, '%',
                             observed_at=self.now, vehicle_id=self.vid)
        self.assertEqual(soh['kind'], 'battery_soh_pct')
        self.assertEqual(soc['kind'], 'battery_soc_pct')

    def test_measured_value_needs_source(self):
        r = self.w.post('/api/evidence/observations', {
            'kind': 'odometer_km', 'value': 100, 'unit': 'km',
            'origin': 'measured', 'observed_at': self.now,
            'vehicle_id': self.vid})
        self.assertEqual(r.status_code, 400)
        self.assertIn('source_id', r.data)

    def test_wrong_subject_and_missing_qualifier(self):
        self.w.observe('odometer_km', 100, 'km', expect=400,
                       observed_at=self.now)  # no vehicle
        self.w.org_value('fuel_price_per_l', 95, 'INR/L', expect=400)

    def test_odometer_must_fit_between_neighbours(self):
        t = timezone.now()
        self.w.observe('odometer_km', 1000, 'km', vehicle_id=self.vid,
                       observed_at=(t - timedelta(days=2)).isoformat())
        r = self.w.observe('odometer_km', 900, 'km', vehicle_id=self.vid,
                           observed_at=(t - timedelta(days=1)).isoformat(),
                           expect=400)
        self.assertIn('supersede', str(r['value']))

    def test_future_reading_rejected(self):
        self.w.observe('odometer_km', 1, 'km', vehicle_id=self.vid,
                       observed_at=(timezone.now() + timedelta(days=1))
                       .isoformat(), expect=400)

    def test_cross_tenant_subject_hidden(self):
        other = World()
        r = self.w.observe('odometer_km', 1, 'km', expect=400,
                           vehicle_id=str(other.vehicle.pk),
                           observed_at=self.now)
        self.assertIn('vehicle_id', r)
        self.assertEqual(other.client.get('/api/evidence/observations').data,
                         [])

    def test_supersede_keeps_original(self):
        first = self.w.observe('odometer_km', 1000, 'km', vehicle_id=self.vid,
                               observed_at=self.now)
        r = self.w.post(
            f'/api/evidence/observations/{first["id"]}/supersede',
            {'kind': 'odometer_km', 'value': 1010, 'unit': 'km',
             'origin': 'measured', 'source_id': str(self.w.record_source.pk),
             'observed_at': self.now, 'vehicle_id': self.vid,
             'correction_reason': 'Typo'})
        self.assertEqual(r.status_code, 201, r.content)
        listed = self.w.client.get('/api/evidence/observations').data
        self.assertEqual([o['id'] for o in listed], [r.data['id']])
        everything = self.w.client.get(
            '/api/evidence/observations?include_superseded=true').data
        self.assertEqual(len(everything), 2)
        self.assertTrue(Observation.objects.filter(pk=first['id']).exists())
        again = self.w.post(
            f'/api/evidence/observations/{first["id"]}/supersede',
            {'kind': 'odometer_km', 'value': 1020, 'unit': 'km',
             'origin': 'measured', 'source_id': str(self.w.record_source.pk),
             'observed_at': self.now, 'vehicle_id': self.vid,
             'correction_reason': 'x'})
        self.assertEqual(again.status_code, 400)

    def test_observations_are_immutable(self):
        o = Observation.objects.get(pk=self.w.observe(
            'odometer_km', 5, 'km', vehicle_id=self.vid,
            observed_at=self.now)['id'])
        o.value = 6
        with self.assertRaises(ValueError):
            o.save()

    def test_evidence_class_and_staleness_reported(self):
        unverified = make_source(self.w.org, 'Tariff order (not checked)',
                                 source_type='tariff_order')
        o = self.w.org_value('electricity_tariff_per_kwh', 8.5, 'INR/kWh',
                             origin='external', source=unverified)
        self.assertEqual(o['evidence_class'], 'unverified_external')
        old = self.w.org_value('fuel_price_per_l', 92, 'INR/L',
                               origin='external',
                               source=make_source(self.w.org, verified=True),
                               qualifiers={'fuel': 'petrol'},
                               effective_from=str(date.today()
                                                  - timedelta(days=200)))
        self.assertEqual(old['evidence_class'], 'stale')
        self.assertTrue(old['stale'])


class ImportTests(TestCase):
    def setUp(self):
        self.w = EvidenceWorld()
        self.src = make_source(self.w.org, 'Fuel price circular',
                               source_type='fuel_price_notice')

    def _upload(self, text):
        f = SimpleUploadedFile('obs.csv', text.encode(), 'text/csv')
        return self.w.client.post('/api/evidence/observations/import',
                                  {'file': f, 'source_id': str(self.src.pk)},
                                  format='multipart')

    def test_all_or_nothing(self):
        header = 'kind,value,unit,qualifiers,origin,effective_from\n'
        good = 'fuel_price_per_l,94.5,INR/L,"{""fuel"": ""diesel""}",external,2026-10-01\n'
        bad = 'fuel_price_per_l,94.5,INR/gal,"{""fuel"": ""diesel""}",external,2026-10-01\n'
        r = self._upload(header + good + bad)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data['failed_rows'][0]['row'], 3)
        self.assertEqual(Observation.objects.count(), 0)
        r = self._upload(header + good)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.data['imported'], 1)
        o = Observation.objects.get()
        self.assertEqual(o.source_id, self.src.pk)

    def test_vehicle_rows_by_registration(self):
        text = ('kind,value,unit,vehicle_registration,origin,observed_at\n'
                f'odometer_km,1200,km,{self.w.vehicle.registration_number},'
                'measured,2026-10-01T09:00:00+05:30\n')
        r = self._upload(text)
        self.assertEqual(r.status_code, 201, r.content)

    def test_unknown_columns_rejected(self):
        r = self._upload('kind,value,unit,price_guess\n')
        self.assertEqual(r.status_code, 400)


class RefreshTests(TestCase):
    """A failed external request never becomes verified current data."""

    def setUp(self):
        self.w = EvidenceWorld()
        self.src = make_source(self.w.org, 'Tariff API', verified=True,
                               retrieval_method='api',
                               url='https://example.invalid/tariffs',
                               source_type='tariff_order')
        self.obs = self.w.org_value('electricity_tariff_per_kwh', 8.0,
                                    'INR/kWh', origin='external',
                                    source=self.src)
        self.assertEqual(self.obs['evidence_class'], 'verified_external')

    def test_failed_refresh_stores_nothing_and_marks_stale(self):
        def broken(source):
            raise ConnectionError('timeout')

        with self.assertRaises(refresh.RefreshFailed):
            refresh.refresh_source(self.src, broken)
        self.src.refresh_from_db()
        self.assertEqual(self.src.last_refresh_status, 'failed')
        self.assertIn('timeout', self.src.last_refresh_error)
        self.assertEqual(Observation.objects.count(), 1)
        got = self.w.client.get(
            f'/api/evidence/observations/{self.obs["id"]}').data
        self.assertEqual(got['evidence_class'], 'stale')
        self.assertIn('refresh', got['stale_reason'])

    def test_partially_invalid_payload_rolls_back(self):
        def adapter(source):
            return [
                {'kind': 'electricity_tariff_per_kwh', 'value': 9.0,
                 'unit': 'INR/kWh', 'effective_from': date.today()},
                {'kind': 'electricity_tariff_per_kwh', 'value': 9.0,
                 'unit': 'USD/kWh', 'effective_from': date.today()},
            ]
        with self.assertRaises(refresh.RefreshFailed):
            refresh.refresh_source(self.src, adapter)
        self.assertEqual(Observation.objects.count(), 1)

    def test_successful_refresh_is_unverified_until_checked(self):
        def adapter(source):
            return [{'kind': 'electricity_tariff_per_kwh', 'value': 9.0,
                     'unit': 'INR/kWh', 'effective_from': date.today()}]
        created = refresh.refresh_source(self.src, adapter)
        self.assertEqual(len(created), 1)
        got = self.w.client.get(
            f'/api/evidence/observations/{created[0].pk}').data
        self.assertEqual(got['origin'], 'external')
        self.assertEqual(got['evidence_class'], 'unverified_external')

    def test_no_adapters_are_configured(self):
        self.assertEqual(refresh.ADAPTERS, {})


class EnergyRecordAndUsageTests(TestCase):
    def setUp(self):
        self.w = EvidenceWorld()
        self.vid = str(self.w.vehicle.pk)

    def fill(self, days_ago, litres, full, odo, expect=201, **kw):
        body = {'vehicle_id': self.vid, 'record_type': 'fuel_fill',
                'occurred_at': (timezone.now() - timedelta(days=days_ago))
                .isoformat(), 'quantity': litres, 'is_full': full,
                'odometer_km': odo, 'origin': 'measured',
                'source_id': str(self.w.record_source.pk)}
        body.update(kw)
        r = self.w.post('/api/evidence/energy-records', body)
        self.assertEqual(r.status_code, expect, r.content)
        return r.data

    def test_usage_profile_measured_planned_and_record_kept_apart(self):
        self.w.daily_odometer(40, 120.0)
        self.fill(30, 40, True, 10000 + 9 * 120)
        self.fill(20, 50, False, None)
        self.fill(10, 52, True, 10000 + 29 * 120)
        r = self.w.client.get(
            f'/api/evidence/vehicles/{self.vid}/usage?window_days=60')
        self.assertEqual(r.status_code, 200, r.content)
        odo = r.data['measured']['odometer']
        self.assertEqual(odo['daily']['sample_count'], 39)
        self.assertEqual(odo['daily']['p90_km'], 120.0)
        self.assertAlmostEqual(odo['annualised_distance_km'], 120 * 365,
                               delta=1)
        fuel = r.data['measured']['fuel_consumption']
        # 20 days * 120 km = 2400 km on 50 + 52 L
        self.assertEqual(fuel['distance_km'], 2400.0)
        self.assertAlmostEqual(fuel['km_per_l'], 2400 / 102, places=3)
        self.assertEqual(r.data['planned']['basis'], 'planned')
        self.assertEqual(r.data['vehicle_record']['basis'],
                         'organization_provided')

    def test_energy_record_validation(self):
        self.fill(1, -1, True, 1, expect=400)
        self.fill(1, 10, True, 1, expect=400, record_type='charging_session')
        r = self.fill(1, 10, True, 1, expect=400, cost=100, currency='USD')
        self.assertIn('currency', r)

    def test_void_keeps_record_and_excludes_it(self):
        rec = self.fill(1, 30, True, 5000)
        r = self.w.post(f'/api/evidence/energy-records/{rec["id"]}/void',
                        {'reason': 'duplicate'})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(EnergyRecord.objects.filter(pk=rec['id']).exists())
        self.assertEqual(self.w.client.get(
            '/api/evidence/energy-records').data, [])


class ExistingApiUnchangedTests(TestCase):
    def test_vehicle_list_shape_unchanged(self):
        w = World()
        r = w.client.get('/api/vehicle')
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.data, list)
        self.assertNotIn('observations', r.data[0])
        r = w.client.get('/api/operations/summary?tz=UTC')
        self.assertEqual(r.status_code, 200)
