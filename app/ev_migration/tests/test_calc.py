"""Numerical tests for the pure calculation layer.

Reference values are computed independently in the tests (closed-form
annuity factors, hand arithmetic written out in comments) rather than by
calling the code under test.
"""

from django.test import SimpleTestCase

from evidence.errors import CalculationError
from ev_migration.calc import emissions, readiness, tco


def annuity(r, n):
    """Closed-form present value of 1 per year for n years."""
    return n if r == 0 else (1 - (1 + r) ** -n) / r


READY = dict(
    design_day_distance_km=180.0, reserve_fraction=0.2,
    usable_battery_kwh=200.0, consumption_kwh_per_km=0.9,
    consumption_basis='vehicle', consumption_source_type='measured_trial',
    charging_efficiency=0.9, required_payload_kg=1000.0,
    candidate_payload_kg=1200.0, charger_power_kw=30.0,
    vehicle_max_charge_kw=22.0, charging_window_hours=10.0,
    current_vehicle_type='truck', candidate_vehicle_type='truck',
    rated_range_km=400.0,
)


def check(result, name):
    return next(c for c in result['checks'] if c['check'] == name)


class ReadinessTests(SimpleTestCase):
    def test_reference_example_meets_requirements(self):
        r = readiness.assess(READY)
        # 180 km * 1.2 = 216 km ; 216 * 0.9 = 194.4 kWh <= 200
        rng = check(r, 'range')
        self.assertEqual(rng['status'], 'pass')
        self.assertAlmostEqual(rng['values']['battery_energy_needed_kwh'],
                               194.4)
        self.assertAlmostEqual(rng['values']['margin_kwh'], 5.6)
        # grid: 180 * 0.9 / 0.9 = 180 kWh at min(30, 22) = 22 kW = 8.182 h
        chg = check(r, 'charging')
        self.assertEqual(chg['values']['effective_power_kw'], 22.0)
        self.assertAlmostEqual(chg['values']['daily_grid_energy_kwh'], 180.0)
        self.assertAlmostEqual(chg['values']['charging_hours'], 8.182)
        self.assertEqual(r['outcome'], 'meets_requirements')
        self.assertTrue(r['eligible_for_scoring'])

    def test_range_failure_blocks_recommendation(self):
        r = readiness.assess(dict(READY, design_day_distance_km=200.0))
        # 200 * 1.2 * 0.9 = 216 kWh > 200
        self.assertEqual(check(r, 'range')['status'], 'fail')
        self.assertEqual(r['outcome'], 'not_suitable')
        self.assertFalse(r['eligible_for_scoring'])

    def test_payload_failure_blocks_recommendation(self):
        r = readiness.assess(dict(READY, required_payload_kg=1500.0))
        self.assertEqual(check(r, 'payload')['status'], 'fail')
        self.assertEqual(r['outcome'], 'not_suitable')
        self.assertFalse(r['eligible_for_scoring'])

    def test_charging_fails_when_window_too_short(self):
        r = readiness.assess(dict(READY, charging_window_hours=8.0))
        # needs 8.18 h, has 8 h
        self.assertEqual(check(r, 'charging')['status'], 'fail')
        self.assertEqual(r['outcome'], 'not_suitable')

    def test_charger_existing_without_power_is_insufficient(self):
        r = readiness.assess(dict(READY, charger_power_kw=None))
        self.assertEqual(check(r, 'charging')['status'], 'insufficient')
        self.assertEqual(r['outcome'], 'insufficient_evidence')
        self.assertFalse(r['eligible_for_scoring'])

    def test_grid_basis_losses_not_applied_twice(self):
        # Grid-side 1.0 kWh/km already includes charging losses.
        r = readiness.assess(dict(READY, consumption_kwh_per_km=1.0,
                                  consumption_basis='grid'))
        chg = check(r, 'charging')
        self.assertAlmostEqual(chg['values']['daily_grid_energy_kwh'], 180.0)
        self.assertAlmostEqual(r['derived']['c_grid_kwh_per_km'], 1.0)
        # battery-side = 1.0 * 0.9 = 0.9 -> 194.4 kWh, as in the vehicle case
        self.assertAlmostEqual(
            check(r, 'range')['values']['battery_energy_needed_kwh'], 194.4)

    def test_vehicle_basis_without_efficiency_cannot_test_charging(self):
        r = readiness.assess(dict(READY, charging_efficiency=None))
        self.assertEqual(check(r, 'charging')['status'], 'insufficient')
        self.assertEqual(check(r, 'range')['status'], 'pass')

    def test_rated_range_is_never_used(self):
        a = readiness.assess(dict(READY, rated_range_km=10.0))
        b = readiness.assess(dict(READY, rated_range_km=9999.0))
        self.assertEqual(a['checks'], b['checks'])

    def test_impossible_values_rejected(self):
        with self.assertRaises(CalculationError):
            readiness.assess(dict(READY, reserve_fraction=1.5))
        with self.assertRaises(CalculationError):
            readiness.assess(dict(READY, consumption_kwh_per_km=900.0))
        with self.assertRaises(CalculationError):
            readiness.assess(dict(READY, charging_efficiency=1.2))
        with self.assertRaises(CalculationError):
            readiness.assess(dict(READY, design_day_distance_km=-5.0))

    def test_manufacturer_rated_consumption_is_flagged(self):
        r = readiness.assess(dict(READY,
                                  consumption_source_type='manufacturer_rated'))
        self.assertTrue(any('manufacturer-rated' in w for w in r['warnings']))


TCO = dict(
    annual_distance_km=30000.0, horizon_years=5, discount_rate=0.10,
    ice_initial_investment=0.0, fuel_economy_km_per_l=10.0,
    fuel_price_per_l=90.0, ice_maintenance_annual=40000.0,
    ev_purchase_price=1_200_000.0, charging_infrastructure_cost=100_000.0,
    ev_grid_kwh_per_km=0.25, electricity_tariff_per_kwh=8.0,
    ev_maintenance_annual=20000.0,
)


class TcoTests(SimpleTestCase):
    def test_reference_npc(self):
        r = tco.compare(TCO)
        af = annuity(0.10, 5)  # 3.790786769...
        # ICE: 30000/10*90 = 270000 fuel + 40000 maintenance
        ice_annual = 30000 / 10 * 90 + 40000
        # EV: 30000*0.25*8 = 60000 energy + 20000 maintenance
        ev_annual = 30000 * 0.25 * 8 + 20000
        self.assertEqual(r['ice']['annual_operating_total'], 310000.0)
        self.assertEqual(r['ev']['annual_operating_total'], 80000.0)
        self.assertAlmostEqual(r['ice']['net_present_cost'],
                               round(ice_annual * af, 2), places=2)
        self.assertAlmostEqual(r['ev']['net_present_cost'],
                               round(1_300_000 + ev_annual * af, 2), places=2)
        self.assertAlmostEqual(r['net_present_savings'],
                               round((ice_annual - ev_annual) * af
                                     - 1_300_000, 2), places=1)
        # 1,300,000 / 230,000 = 5.652 years (undiscounted)
        self.assertAlmostEqual(r['simple_payback_years'], 5.65)
        # never recovered within 5 discounted years
        self.assertIsNone(r['discounted_payback_years'])
        # cost per km (operating, undiscounted): 310000/30000
        self.assertAlmostEqual(r['ice']['operating_cost_per_km'], 10.3333,
                               places=4)
        # levelised: NPC / (30000 * af)
        self.assertAlmostEqual(r['ev']['levelised_cost_per_km'],
                               (1_300_000 + ev_annual * af) / (30000 * af),
                               places=4)

    def test_cash_flow_rows_sum_to_npc(self):
        r = tco.compare(dict(TCO, ice_residual_value=50000.0,
                             ev_residual_value=400000.0))
        for side in ('ice', 'ev'):
            total = sum(row['discounted_net_cost']
                        for row in r[side]['cash_flows'])
            self.assertAlmostEqual(total, r[side]['net_present_cost'],
                                   delta=0.05)
        # residual credited only in the final year
        self.assertEqual(r['ev']['cash_flows'][-1]['residual_credit'],
                         400000.0)
        self.assertEqual(r['ev']['cash_flows'][1]['residual_credit'], 0.0)
        # NPC = I + C*af - R/(1.1^5)
        expected = 1_300_000 + 80000 * annuity(0.1, 5) - 400000 / 1.1 ** 5
        self.assertAlmostEqual(r['ev']['net_present_cost'], expected,
                               delta=0.01)

    def test_discounted_payback_interpolates(self):
        # extra 300,000; saving 230,000/yr; r = 0
        r = tco.compare(dict(TCO, discount_rate=0.0, ev_purchase_price=200000.0))
        self.assertAlmostEqual(r['simple_payback_years'], 300000 / 230000,
                               places=2)
        self.assertAlmostEqual(r['discounted_payback_years'],
                               300000 / 230000, places=2)

    def test_payback_not_applicable_when_ev_costs_more_to_run(self):
        # EV cheaper upfront (retain baseline 0 vs EV 0) but dearer to run
        r = tco.compare(dict(TCO, ev_purchase_price=0.0,
                             charging_infrastructure_cost=0.0,
                             ev_maintenance_annual=400000.0))
        self.assertLess(r['annual_operating_saving'], 0)
        self.assertIsNone(r['simple_payback_years'])
        self.assertIsNone(r['discounted_payback_years'])
        # no extra upfront and cheaper to run -> nothing to recover
        r = tco.compare(dict(TCO, ev_purchase_price=0.0,
                             charging_infrastructure_cost=0.0))
        self.assertEqual(r['simple_payback_years'], 0.0)
        self.assertEqual(r['discounted_payback_years'], 0.0)

    def test_missing_fuel_economy_is_insufficient_not_zero(self):
        r = tco.compare(dict(TCO, fuel_economy_km_per_l=None))
        self.assertEqual(r['status'], 'insufficient')
        self.assertIn('fuel_economy_km_per_l', r['missing'])
        self.assertNotIn('ice', r)

    def test_zero_fuel_economy_rejected(self):
        with self.assertRaises(CalculationError):
            tco.compare(dict(TCO, fuel_economy_km_per_l=0.0))

    def test_tariff_changes_only_ev_energy(self):
        base = tco.compare(TCO)
        more = tco.compare(dict(TCO, electricity_tariff_per_kwh=10.0))
        self.assertEqual(more['ice'], base['ice'])
        # 7500 kWh * 2 more per kWh
        self.assertEqual(more['ev']['annual_costs']['energy'] -
                         base['ev']['annual_costs']['energy'], 15000.0)
        self.assertEqual(more['ev']['annual_costs']['maintenance'],
                         base['ev']['annual_costs']['maintenance'])
        self.assertAlmostEqual(
            more['ev']['net_present_cost'] - base['ev']['net_present_cost'],
            15000 * annuity(0.10, 5), delta=0.02)
        self.assertLess(more['net_present_savings'],
                        base['net_present_savings'])

    def test_discount_rate_changes_present_values(self):
        low = tco.compare(dict(TCO, discount_rate=0.05))
        high = tco.compare(dict(TCO, discount_rate=0.12))
        self.assertAlmostEqual(low['ice']['net_present_cost'],
                               310000 * annuity(0.05, 5), delta=0.01)
        self.assertAlmostEqual(high['ice']['net_present_cost'],
                               310000 * annuity(0.12, 5), delta=0.01)
        self.assertGreater(low['ice']['net_present_cost'],
                           high['ice']['net_present_cost'])
        # year-0 investment is not discounted
        self.assertEqual(low['ev']['cash_flows'][0]['discounted_net_cost'],
                         high['ev']['cash_flows'][0]['discounted_net_cost'])
        # undiscounted payback does not depend on r
        self.assertEqual(low['simple_payback_years'],
                         high['simple_payback_years'])

    def test_unverified_incentive_not_applied(self):
        incentives = [
            {'name': 'Verified scheme', 'amount': 150000.0, 'included': True},
            {'name': 'Unverified scheme', 'amount': 500000.0,
             'included': False, 'reason': 'source not verified'},
        ]
        r = tco.compare(dict(TCO, incentives=incentives))
        self.assertEqual(r['ev']['initial_investment'],
                         1_300_000.0 - 150000.0)
        self.assertEqual(r['ev']['investment_breakdown']['incentives_applied'],
                         -150000.0)
        self.assertTrue(any('Unverified scheme' in n for n in r['notes']))

    def test_one_sided_optional_cost_excluded_from_both(self):
        r = tco.compare(dict(TCO, ice_insurance_annual=30000.0))
        self.assertNotIn('insurance', r['ice']['annual_costs'])
        self.assertNotIn('insurance', r['ev']['annual_costs'])
        self.assertIn('insurance_annual', r['excluded_categories'])
        both = tco.compare(dict(TCO, ice_insurance_annual=30000.0,
                                ev_insurance_annual=45000.0))
        self.assertEqual(both['ice']['annual_costs']['insurance'], 30000.0)
        self.assertEqual(both['ev']['annual_costs']['insurance'], 45000.0)

    def test_sensitivity_directions(self):
        rows = {r['input']: r for r in tco.sensitivity(TCO, 0.2)}
        fuel = rows['fuel_price_per_l']
        self.assertGreater(fuel['high_net_present_savings'],
                           fuel['low_net_present_savings'])
        price = rows['ev_purchase_price']
        self.assertLess(price['high_net_present_savings'],
                        price['low_net_present_savings'])
        # +20% EV price = 240,000 more upfront, undiscounted at t=0
        self.assertAlmostEqual(
            price['base_net_present_savings'] -
            price['high_net_present_savings'], 240000.0, delta=0.02)

    def test_invalid_parameters(self):
        with self.assertRaises(CalculationError):
            tco.compare(dict(TCO, discount_rate=-0.01))
        with self.assertRaises(CalculationError):
            tco.compare(dict(TCO, horizon_years=2.5))
        with self.assertRaises(CalculationError):
            tco.compare(dict(TCO, annual_distance_km=-1.0))


EM = dict(annual_distance_km=30000.0, fuel_economy_km_per_l=10.0,
          fuel_factor_kgco2e_per_l=2.68, fuel_boundary='tank_to_wheel',
          ev_grid_kwh_per_km=0.25, grid_factor_kgco2e_per_kwh=0.71,
          grid_boundary='generation')


class EmissionsTests(SimpleTestCase):
    def test_reference_values(self):
        r = emissions.compare(EM)
        self.assertEqual(r['baseline']['kgco2e_per_year'], 8040.0)  # 3000*2.68
        self.assertEqual(r['ev']['kgco2e_per_year'], 5325.0)  # 7500*0.71
        self.assertEqual(r['reduction_kgco2e_per_year'], 2715.0)
        self.assertEqual(r['comparison_boundary'], 'operational')

    def test_grid_factor_changes_only_ev(self):
        a = emissions.compare(EM)
        b = emissions.compare(dict(EM, grid_factor_kgco2e_per_kwh=0.5))
        self.assertEqual(a['baseline'], b['baseline'])
        self.assertEqual(b['ev']['kgco2e_per_year'], 3750.0)  # 7500*0.5

    def test_ev_is_not_labelled_zero(self):
        r = emissions.compare(EM)
        self.assertGreater(r['ev']['kgco2e_per_year'], 0)
        self.assertEqual(r['ev']['boundary'], 'generation')
        self.assertIn('tailpipe', r['ev']['note'])
        self.assertIn('vehicle and battery manufacturing (lifecycle)',
                      r['excluded'])

    def test_missing_factor_is_insufficient(self):
        r = emissions.compare(dict(EM, grid_factor_kgco2e_per_kwh=None))
        self.assertEqual(r['status'], 'insufficient')
