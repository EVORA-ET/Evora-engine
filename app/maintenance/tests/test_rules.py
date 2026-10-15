from datetime import date

from django.test import SimpleTestCase

from maintenance import rules as R

TODAY = date(2026, 10, 15)
PURCHASE = date(2024, 1, 1)
SRC = {'id': 's1', 'title': 'Owner manual'}


def req(**kw):
    base = dict(id='q1', name='Engine oil', interval_km=10000.0,
                interval_days=365, warning_km=1000.0, warning_days=30,
                source=SRC)
    base.update(kw)
    return R.Requirement(**base)


def odo(km, stale=False):
    return R.Reading(value=km, at='2026-10-14T10:00:00+00:00', stale=stale,
                     stale_reason='old' if stale else '')


def service(on, km, rid='q1', eid='e1'):
    return R.Event(id=eid, event_type='scheduled_service', occurred_on=on,
                   odometer_km=km, requirement_id=rid)


def evaluate(reqs, events, odometer=None, battery=None):
    return R.evaluate(today=TODAY, purchase_date=PURCHASE, requirements=reqs,
                      events=events, odometer=odometer,
                      battery=battery or {'applicable': False},
                      window_start=date(2025, 10, 15), window_end=TODAY)


class ServiceRuleTests(SimpleTestCase):
    def test_ok(self):
        r = evaluate([req()], [service(date(2026, 6, 1), 50000.0)],
                     odo(52000.0))
        s = r['service_requirements'][0]
        self.assertEqual(s['status'], 'ok')
        self.assertEqual(s['dimensions']['distance']['km_remaining'], 8000.0)
        self.assertEqual(s['dimensions']['time']['due_date'], '2027-06-01')

    def test_due_soon_by_distance(self):
        r = evaluate([req()], [service(date(2026, 6, 1), 50000.0)],
                     odo(59500.0))
        self.assertEqual(r['service_requirements'][0]['status'], 'due_soon')

    def test_overdue_by_time(self):
        r = evaluate([req()], [service(date(2025, 9, 1), 50000.0)],
                     odo(51000.0))
        s = r['service_requirements'][0]
        self.assertEqual(s['dimensions']['time']['status'], 'overdue')
        self.assertEqual(s['status'], 'overdue')

    def test_no_history_distance_undetermined_time_from_purchase(self):
        r = evaluate([req()], [], odo(30000.0))
        s = r['service_requirements'][0]
        self.assertEqual(s['dimensions']['distance']['status'],
                         'undetermined')
        self.assertIn('purchase date',
                      s['dimensions']['time']['baseline_basis'])
        # purchase 2024-01-01 + 365 days -> overdue
        self.assertEqual(s['status'], 'overdue')

    def test_no_odometer_undetermined(self):
        r = evaluate([req(interval_days=None, warning_days=None)],
                     [service(date(2026, 6, 1), 50000.0)], None)
        self.assertEqual(r['service_requirements'][0]['status'],
                         'undetermined')

    def test_stale_odometer_flagged(self):
        r = evaluate([req()], [service(date(2026, 6, 1), 50000.0)],
                     odo(52000.0, stale=True))
        self.assertTrue(any('stale' in n for n in
                            r['service_requirements'][0]['notes']))


class NoFabricationTests(SimpleTestCase):
    def test_missing_history_generates_no_prediction(self):
        r = evaluate([], [], None)
        self.assertEqual(r['service_requirements'], [])
        self.assertEqual(r['open_faults'], [])
        self.assertFalse(r['predictive_assessment']['available'])
        self.assertEqual(r['records']['event_count'], 0)
        self.assertIsNone(r['records']['recorded_downtime_hours'])
        self.assertEqual(r['records']['recorded_cost_by_currency'], {})
        self.assertTrue(any('No maintenance history' in m
                            for m in r['messages']))
        for key in ('failure_probability', 'remaining_useful_life',
                    'risk_score'):
            self.assertNotIn(key, r)

    def test_open_and_resolved_faults(self):
        f1 = R.Event(id='f1', event_type='fault_report',
                     occurred_on=date(2026, 10, 1), fault_code='P0420',
                     fault_severity='medium')
        f2 = R.Event(id='f2', event_type='fault_report',
                     occurred_on=date(2026, 9, 1), description='Brake noise')
        fix = R.Event(id='x', event_type='repair',
                      occurred_on=date(2026, 9, 3), resolves_id='f2')
        r = evaluate([], [f1, f2, fix])
        self.assertEqual([f['event_id'] for f in r['open_faults']], ['f1'])
        self.assertEqual(r['open_faults'][0]['days_open'], 14)
        self.assertEqual(r['open_faults'][0]['status'], 'review_required')

    def test_costs_missing_are_counted_not_zeroed(self):
        events = [
            R.Event(id='a', event_type='repair', occurred_on=date(2026, 5, 1),
                    cost=1000.0, currency='INR', downtime_hours=4.0),
            R.Event(id='b', event_type='repair', occurred_on=date(2026, 6, 1)),
        ]
        rec = evaluate([], events)['records']
        self.assertEqual(rec['recorded_cost_by_currency'], {'INR': 1000.0})
        self.assertEqual(rec['events_without_cost'], 1)
        self.assertEqual(rec['recorded_downtime_hours'], 4.0)
        self.assertEqual(rec['events_with_downtime'], 1)


class BatteryTests(SimpleTestCase):
    def test_soc_is_not_used_as_health(self):
        soc = R.Reading(value=15.0, at='2026-10-14', method='')
        b = R.battery_check(is_ev=True, nominal_kwh=60.0, soh=None,
                            threshold=R.Reading(70.0, '2025-01-01'),
                            capacity=None, soc=soc)
        self.assertIsNone(b['state_of_health'])
        self.assertIsNone(b['warranty_check'])
        self.assertEqual(b['state_of_charge']['pct'], 15.0)
        self.assertTrue(any('cannot be assessed' in m for m in b['messages']))

    def test_soh_vs_warranty_threshold(self):
        b = R.battery_check(
            is_ev=True, nominal_kwh=60.0,
            soh=R.Reading(68.0, '2026-10-01', method='capacity_test'),
            threshold=R.Reading(70.0, '2025-01-01'), capacity=None, soc=None)
        self.assertEqual(b['warranty_check']['status'], 'below_threshold')

    def test_capacity_retention_labels(self):
        measured = R.battery_check(
            is_ev=True, nominal_kwh=60.0, soh=None, threshold=None, soc=None,
            capacity=R.Reading(54.0, '2026-10-01', method='capacity_test'))
        self.assertEqual(measured['capacity_retention']['retention_pct'], 90.0)
        self.assertEqual(measured['capacity_retention']['basis'], 'measured')
        est = R.battery_check(
            is_ev=True, nominal_kwh=60.0, soh=None, threshold=None, soc=None,
            capacity=R.Reading(54.0, '2026-10-01', method='bms_estimate'))
        self.assertEqual(est['capacity_retention']['basis'], 'estimated')

    def test_not_applicable_to_ice(self):
        self.assertEqual(R.battery_check(is_ev=False, nominal_kwh=None,
                                         soh=None, threshold=None,
                                         capacity=None, soc=None),
                         {'applicable': False})
