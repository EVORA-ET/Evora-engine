"""End-to-end migration assessments through the API, against stored
evidence, including traceability and reproducibility."""

from datetime import date, timedelta

from django.test import TestCase

from evidence.assessments import reproduce
from evidence.models import Assessment
from evidence.tests.fixtures import EvidenceWorld, make_source

READINESS = '/api/ev-migration/assessments/readiness'
TCO = '/api/ev-migration/assessments/tco'
EMISSIONS = '/api/ev-migration/assessments/emissions'


class MigrationApiBase(TestCase):
    def setUp(self):
        w = self.w = EvidenceWorld()
        self.vid = str(w.vehicle.pk)
        self.cid = str(w.candidate.pk)
        self.depot = str(w.depot_a.pk)
        self.cid_src = make_source(w.org, 'Dealer quotation Q-17',
                                   verified=True,
                                   source_type='procurement_quotation')
        self.tariff_src = make_source(w.org, 'Utility bill Sep 2026',
                                      verified=True,
                                      source_type='utility_bill')
        w.daily_odometer(40, 150.0)
        w.org_value('charger_power_kw', 30, 'kW', origin='external',
                    source=self.tariff_src, depot_id=self.depot,
                    qualifiers={'connector': 'ac'})
        w.org_value('charging_window_hours', 10, 'h',
                    origin='organization_provided', depot_id=self.depot)
        w.org_value('charging_efficiency_fraction', 0.9, 'fraction',
                    origin='external', source=self.cid_src,
                    ev_candidate_id=self.cid)

    def readiness(self, **body):
        data = {'vehicle_id': self.vid, 'ev_candidate_id': self.cid,
                'reserve_fraction': 0.2, 'charging_connector': 'ac',
                'design_day_distance': {'basis': 'measured_p90'}}
        data.update(body)
        return self.w.post(READINESS, data)

    def add_costs(self):
        w = self.w
        w.org_value('fuel_price_per_l', 90, 'INR/L', origin='external',
                    source=self.tariff_src, qualifiers={'fuel': 'diesel'})
        self.tariff = w.org_value('electricity_tariff_per_kwh', 8, 'INR/kWh',
                                  origin='external', source=self.tariff_src)
        w.org_value('vehicle_purchase_price', 1200000, 'INR',
                    origin='external', source=self.cid_src,
                    ev_candidate_id=self.cid,
                    qualifiers={'purpose': 'candidate_quote'})
        w.org_value('charging_infrastructure_cost', 100000, 'INR',
                    origin='external', source=self.cid_src,
                    ev_candidate_id=self.cid)
        w.org_value('maintenance_cost_annual', 20000, 'INR/year',
                    origin='external', source=self.cid_src,
                    ev_candidate_id=self.cid)
        w.org_value('maintenance_cost_annual', 40000, 'INR/year',
                    origin='measured', vehicle_id=self.vid)

    def tco(self, **body):
        data = {'vehicle_id': self.vid, 'ev_candidate_id': self.cid,
                'horizon_years': 5, 'discount_rate': 0.1,
                'baseline': 'retain_existing'}
        data.update(body)
        return self.w.post(TCO, data)


class ReadinessApiTests(MigrationApiBase):
    def test_measured_readiness_with_full_trace(self):
        r = self.readiness()
        self.assertEqual(r.status_code, 201, r.content)
        res = r.data['result']
        # p90 of 150 km/day; 150*1.2*0.9 = 162 kWh <= 200; 150 kWh grid at 22 kW
        self.assertEqual(res['outcome'], 'meets_requirements')
        inputs = r.data['input_snapshot']['inputs']
        self.assertEqual(inputs['design_day_distance_km']['value'], 150.0)
        self.assertEqual(inputs['design_day_distance_km']['origin'],
                         'measured')
        self.assertEqual(inputs['charger_power_kw']['source']['title'],
                         'Utility bill Sep 2026')
        # Payload falls back to the legacy vehicle field, labelled as such.
        self.assertEqual(inputs['required_payload_kg']['evidence_class'],
                         'organization_provided')
        self.assertIn('Like-for-like',
                      inputs['required_payload_kg']['transformation'])
        # Charging window and payload are organization-provided (no source),
        # everything else is measured/verified -> moderate, with reasons.
        self.assertEqual(r.data['evidence_level'], 'moderate')
        reasons = ' '.join(r.data['evidence_reasons'])
        self.assertIn('charging_window_hours', reasons)
        self.assertIn('required_payload_kg', reasons)
        self.assertTrue(r.data['fingerprint'])

    def test_missing_charger_power_is_insufficient(self):
        r = self.readiness(charging_connector='dc')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.data['status'], 'insufficient_evidence')
        self.assertIn('charger_power_kw', r.data['missing_inputs'])
        self.assertEqual(r.data['evidence_level'], 'insufficient')

    def test_payload_failure_cannot_be_approved(self):
        r = self.readiness(inputs={'required_payload_kg': {
            'value': 1500, 'unit': 'kg', 'origin': 'organization_provided',
            'note': 'Max consignment'}})
        self.assertEqual(r.data['result']['outcome'], 'not_suitable')
        d = self.w.post('/api/ev-migration/decisions', {
            'vehicle_id': self.vid, 'ev_candidate_id': self.cid,
            'decision': 'approve_transition',
            'readiness_assessment_id': r.data['id'],
            'rationale': 'Cheap to run'})
        self.assertEqual(d.status_code, 400)
        self.assertIn('decision', d.data)
        d = self.w.post('/api/ev-migration/decisions', {
            'vehicle_id': self.vid, 'ev_candidate_id': self.cid,
            'decision': 'reject', 'readiness_assessment_id': r.data['id'],
            'rationale': 'Payload too low'})
        self.assertEqual(d.status_code, 201, d.content)
        self.assertEqual(str(d.data['readiness_assessment_id']),
                         str(r.data['id']))

    def test_short_charging_window_fails(self):
        r = self.readiness(inputs={'charging_window_hours': {
            'value': 5, 'unit': 'h', 'origin': 'organization_provided'}})
        res = r.data['result']
        chg = next(c for c in res['checks'] if c['check'] == 'charging')
        self.assertEqual(chg['status'], 'fail')
        self.assertEqual(res['outcome'], 'not_suitable')

    def test_inline_values_cannot_claim_measured(self):
        r = self.readiness(inputs={'charging_window_hours': {
            'value': 10, 'unit': 'h', 'origin': 'measured'}})
        self.assertEqual(r.status_code, 400)

    def test_ev_vehicle_rejected(self):
        self.w.vehicle.fuel_type = 'ev'
        self.w.vehicle.save(update_fields=['fuel_type'])
        self.assertEqual(self.readiness().status_code, 400)


class TcoApiTests(MigrationApiBase):
    def setUp(self):
        super().setUp()
        self.add_costs()

    def test_tco_from_measured_and_legacy_evidence(self):
        r = self.tco()
        self.assertEqual(r.status_code, 201, r.content)
        res = r.data['result']
        self.assertEqual(res['status'], 'ok')
        inputs = r.data['input_snapshot']['inputs']
        # 150 km/day measured -> annualised ~54,750 km
        self.assertEqual(inputs['annual_distance_km']['origin'], 'measured')
        self.assertAlmostEqual(inputs['annual_distance_km']['value'],
                               150 * 365, delta=1)
        # No fuel records: falls back to the legacy mileage field (12.5)
        self.assertEqual(inputs['fuel_economy_km_per_l']['value'], 12.5)
        self.assertEqual(inputs['fuel_economy_km_per_l']['evidence_class'],
                         'organization_provided')
        # grid consumption derived once: 0.9 / 0.9 = 1.0 kWh/km
        self.assertEqual(inputs['ev_grid_kwh_per_km']['value'], 1.0)
        self.assertEqual(r.data['evidence_level'], 'moderate')
        km = inputs['annual_distance_km']['value']
        self.assertAlmostEqual(res['ev']['annual_costs']['energy'],
                               km * 1.0 * 8, delta=0.01)
        self.assertAlmostEqual(res['ice']['annual_costs']['energy'],
                               km / 12.5 * 90, delta=0.01)

    def test_missing_fuel_economy_never_zero(self):
        self.w.vehicle.mileage_kmpl = 0
        self.w.vehicle.save(update_fields=['mileage_kmpl'])
        r = self.tco()
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.data['status'], 'insufficient_evidence')
        self.assertIn('fuel_economy_km_per_l', r.data['missing_inputs'])
        self.assertNotIn('ice', r.data['result'])

    def test_unverified_incentive_excluded(self):
        unverified = make_source(self.w.org, 'News article about subsidy',
                                 source_type='other')
        self.w.org_value('incentive_amount', 300000, 'INR',
                         origin='external', source=unverified,
                         ev_candidate_id=self.cid,
                         qualifiers={'scheme': 'Rumoured scheme'})
        verified = make_source(self.w.org, 'State EV policy notification',
                               verified=True,
                               source_type='government_notification')
        self.w.org_value('incentive_amount', 50000, 'INR', origin='external',
                         source=verified, ev_candidate_id=self.cid,
                         qualifiers={'scheme': 'State scheme'})
        r = self.tco()
        res = r.data['result']
        self.assertEqual(res['ev']['investment_breakdown']
                         ['incentives_applied'], -50000.0)
        incs = {i['name']: i for i in
                r.data['input_snapshot']['context']['incentives']}
        self.assertFalse(incs['Rumoured scheme']['included'])
        self.assertTrue(incs['State scheme']['included'])

    def test_tariff_change_creates_new_result_old_stays_reproducible(self):
        first = self.tco()
        self.assertEqual(first.status_code, 201)
        old = Assessment.objects.get(pk=first.data['id'])
        # Correct the tariff: supersede the observation.
        r = self.w.post(
            f'/api/evidence/observations/{self.tariff["id"]}/supersede',
            {'kind': 'electricity_tariff_per_kwh', 'value': 10,
             'unit': 'INR/kWh', 'origin': 'external',
             'source_id': str(self.tariff_src.pk),
             'effective_from': str(date.today() - timedelta(days=1)),
             'correction_reason': 'New tariff order'})
        self.assertEqual(r.status_code, 201, r.content)
        second = self.tco()
        self.assertEqual(second.data['input_snapshot']['inputs']
                         ['electricity_tariff_per_kwh']['value'], 10.0)
        self.assertLess(second.data['result']['net_present_savings'],
                        first.data['result']['net_present_savings'])
        self.assertEqual(second.data['result']['ice'],
                         first.data['result']['ice'])
        # The first assessment is untouched and still reproduces exactly.
        old.refresh_from_db()
        self.assertEqual(old.input_snapshot['inputs']
                         ['electricity_tariff_per_kwh']['value'], 8.0)
        rep = self.w.post(f'/api/evidence/assessments/{old.pk}/reproduce', {})
        self.assertEqual(rep.status_code, 200)
        self.assertTrue(rep.data['result_matches'])
        self.assertTrue(rep.data['fingerprint_matches'])
        self.assertNotEqual(old.fingerprint, second.data['fingerprint'])

    def test_same_snapshot_same_result(self):
        a = self.tco(sensitivity_fraction=0.2)
        b = self.tco(sensitivity_fraction=0.2)
        self.assertEqual(a.data['result'], b.data['result'])
        self.assertEqual(a.data['fingerprint'], b.data['fingerprint'])
        self.assertNotEqual(a.data['id'], b.data['id'])
        self.assertTrue(reproduce(Assessment.objects.get(
            pk=a.data['id']))['result_matches'])

    def test_discount_rate_changes_present_values(self):
        low = self.tco(discount_rate=0.05).data['result']
        high = self.tco(discount_rate=0.12).data['result']
        self.assertGreater(low['ice']['net_present_cost'],
                           high['ice']['net_present_cost'])
        self.assertEqual(low['ice']['annual_operating_total'],
                         high['ice']['annual_operating_total'])

    def test_assessments_are_immutable(self):
        a = Assessment.objects.get(pk=self.tco().data['id'])
        a.result = {}
        with self.assertRaises(ValueError):
            a.save()

    def test_replace_with_ice_requires_replacement_quote(self):
        missing = self.tco(baseline='replace_with_ice')
        self.assertEqual(missing.data['status'], 'insufficient_evidence')
        self.assertIn('ice_initial_investment',
                      missing.data['missing_inputs'])
        hist = self.w.org_value('vehicle_purchase_price', 900000, 'INR',
                                origin='external', source=self.cid_src,
                                vehicle_id=self.vid,
                                qualifiers={'purpose': 'historical_purchase'})
        bad = self.tco(baseline='replace_with_ice', inputs={
            'ice_replacement_price': {'observation_id': hist['id']}})
        self.assertEqual(bad.status_code, 400)
        quote = self.w.org_value('vehicle_purchase_price', 950000, 'INR',
                                 origin='external', source=self.cid_src,
                                 vehicle_id=self.vid,
                                 qualifiers={'purpose': 'replacement_quote'})
        ok = self.tco(baseline='replace_with_ice', inputs={
            'ice_replacement_price': {'observation_id': quote['id']}})
        self.assertEqual(ok.status_code, 201, ok.content)
        self.assertEqual(ok.data['result']['ice']['initial_investment'],
                         950000.0)

    def test_unsupported_fuel(self):
        self.w.vehicle.fuel_type = 'cng'
        self.w.vehicle.save(update_fields=['fuel_type'])
        self.assertEqual(self.tco().status_code, 400)


class EmissionsApiTests(MigrationApiBase):
    def setUp(self):
        super().setUp()
        src = make_source(self.w.org, 'Emission factor publication',
                          verified=True,
                          source_type='emission_factor_publication')
        self.w.org_value('fuel_emission_factor_kgco2e_per_l', 2.68,
                         'kgCO2e/L', origin='external', source=src,
                         qualifiers={'fuel': 'diesel',
                                     'boundary': 'tank_to_wheel'})
        self.grid = self.w.org_value(
            'grid_emission_factor_kgco2e_per_kwh', 0.71, 'kgCO2e/kWh',
            origin='external', source=src,
            qualifiers={'boundary': 'generation'})
        self.src = src

    def run_(self, **kw):
        body = {'vehicle_id': self.vid, 'ev_candidate_id': self.cid,
                'fuel_boundary': 'tank_to_wheel',
                'grid_boundary': 'generation'}
        body.update(kw)
        return self.w.post(EMISSIONS, body)

    def test_grid_factor_change_affects_ev_only(self):
        a = self.run_()
        self.assertEqual(a.status_code, 201, a.content)
        self.w.post(
            f'/api/evidence/observations/{self.grid["id"]}/supersede',
            {'kind': 'grid_emission_factor_kgco2e_per_kwh', 'value': 0.6,
             'unit': 'kgCO2e/kWh', 'origin': 'external',
             'source_id': str(self.src.pk),
             'effective_from': str(date.today() - timedelta(days=1)),
             'qualifiers': {'boundary': 'generation'},
             'correction_reason': 'New edition'})
        b = self.run_()
        ra, rb = a.data['result'], b.data['result']
        self.assertEqual(ra['baseline'], rb['baseline'])
        self.assertAlmostEqual(rb['ev']['kgco2e_per_year'] /
                               ra['ev']['kgco2e_per_year'], 0.6 / 0.71,
                               places=3)
        self.assertEqual(a.data['input_snapshot']['inputs']
                         ['grid_factor_kgco2e_per_kwh']['value'], 0.71)

    def test_no_factor_for_requested_boundary(self):
        r = self.run_(grid_boundary='consumption_incl_td_losses')
        self.assertEqual(r.data['status'], 'insufficient_evidence')
        self.assertIn('grid_factor_kgco2e_per_kwh', r.data['missing_inputs'])
