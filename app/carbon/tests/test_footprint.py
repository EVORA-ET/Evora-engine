from datetime import date, timedelta

from django.utils import timezone

from common.testing import World, make_vehicle
from evidence.models import EnergyRecord
from ev_migration.tests.test_api import MigrationApiBase

FP = '/api/carbon/footprint'


class FootprintTests(MigrationApiBase):
    def setUp(self):
        super().setUp()
        w = self.w
        self.factor_src = self.tariff_src
        w.org_value('fuel_emission_factor_kgco2e_per_l', 2.68, 'kgCO2e/L',
                    source=self.factor_src,
                    qualifiers={'fuel': 'diesel', 'boundary': 'tank_to_wheel'},
                    effective_from=str(date.today() - timedelta(days=900)))
        w.org_value('grid_emission_factor_kgco2e_per_kwh', 0.7, 'kgCO2e/kWh',
                    source=self.factor_src,
                    qualifiers={'boundary': 'generation'},
                    effective_from=str(date.today() - timedelta(days=900)))

    def fill(self, days_ago, litres, vehicle=None):
        return EnergyRecord.objects.create(
            organization=self.w.org, vehicle=vehicle or self.w.vehicle,
            record_type='fuel_fill',
            occurred_at=timezone.now() - timedelta(days=days_ago),
            quantity=litres, is_full=True, origin='organization_provided')

    def get(self, **params):
        start = params.pop('start', str(date.today() - timedelta(days=29)))
        end = params.pop('end', str(date.today()))
        qs = '&'.join(f'{k}={v}' for k, v in {'start': start, 'end': end,
                                              **params}.items())
        r = self.w.client.get(f'{FP}?{qs}')
        self.assertEqual(r.status_code, 200, r.content)
        return r.data

    def test_no_records_is_insufficient_not_zero(self):
        d = self.get()
        self.assertEqual(d['status'], 'insufficient')
        self.assertIsNone(d['totals']['kgco2e'])
        self.assertFalse(d['boundary']['lifecycle_included'])

    def test_fuel_emissions_coverage_and_exclusions(self):
        self.fill(5, 40)
        cng = make_vehicle(self.w.org, 'CNG1', fuel_type='cng')
        self.fill(6, 10, vehicle=cng)
        d = self.get()
        self.assertEqual(d['status'], 'ok')
        self.assertAlmostEqual(d['totals']['kgco2e'], 107.2, places=1)
        self.assertIsNone(d['totals']['electricity_kgco2e'])
        cov = d['coverage']
        self.assertEqual(cov['reporting_vehicles'], 2)
        self.assertEqual(cov['records_counted'], 1)
        self.assertTrue(any('CNG' in e['reason']
                            for e in cov['records_excluded']))
        self.assertEqual(sum(m['kgco2e'] or 0 for m in d['months']),
                         d['totals']['kgco2e'])

    def test_previous_period_comparison(self):
        self.fill(5, 40)
        self.fill(40, 50)
        d = self.get()
        prev = d['previous']
        self.assertTrue(prev['comparable'], prev)
        self.assertAlmostEqual(prev['change_kgco2e'], -26.8, places=1)
        self.assertAlmostEqual(prev['change_pct'], -20.0, places=1)
        other = make_vehicle(self.w.org, 'NEW1')
        self.fill(3, 10, vehicle=other)
        self.assertFalse(self.get()['previous']['comparable'])

    def test_missing_boundary_factor(self):
        self.fill(5, 40)
        d = self.get(fuel_boundary='well_to_wheel',
                     grid_boundary='consumption_incl_td_losses')
        self.assertEqual(d['status'], 'insufficient')
        self.assertIn('fuel_emission_factor_kgco2e_per_l', d['missing'])
        b = {(x['fuel_boundary'], x['grid_boundary']): x
             for x in d['boundaries']}
        self.assertEqual(b[('tank_to_wheel', 'generation')]['fuel_factors'],
                         ['diesel'])

    def test_scenario_needs_a_suitable_model_then_models_it(self):
        self.fill(5, 40)
        sc = self.get()['scenario']
        self.assertEqual(sc['status'], 'insufficient')
        r = self.readiness()
        self.assertEqual(r.data['result']['outcome'], 'meets_requirements')
        sc = self.get()['scenario']
        self.assertEqual(sc['status'], 'ok', sc)
        v = sc['vehicles'][0]
        self.assertEqual(v['distance_basis'], 'measured')  # odometer readings
        self.assertGreater(v['ev_kgco2e'], 0)  # EVs are not zero-emission
        full = sc['shares'][-1]
        half = sc['shares'][1]
        self.assertAlmostEqual(half['reduction_kgco2e'] * 2,
                               full['reduction_kgco2e'], places=0)

    def test_pdf_report_and_isolation(self):
        self.fill(5, 40)
        r = self.w.client.get('/api/carbon/report.pdf?scenario_pct=50')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/pdf')
        self.assertTrue(r.content.startswith(b'%PDF'))
        self.assertIn('evora-carbon-report-', r['Content-Disposition'])
        self.assertEqual(self.w.client.get(
            '/api/carbon/report.pdf?scenario_pct=33').status_code, 400)
        self.assertEqual(self.w.client.get(
            f'{FP}?start=2026-02-01&end=2026-01-01').status_code, 400)
        other = World()
        d = other.client.get(FP).data
        self.assertIsNone(d['totals']['kgco2e'])
