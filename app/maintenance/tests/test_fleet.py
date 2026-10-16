from datetime import date, timedelta

from django.test import TestCase
from django.utils import timezone

from common.testing import World, make_vehicle
from evidence.tests.fixtures import EvidenceWorld, make_source


class FleetStatusTests(TestCase):
    def setUp(self):
        self.w = EvidenceWorld()
        self.manual = make_source(self.w.org, 'Manual', verified=True)

    def test_empty_fleet_has_no_fabricated_counts(self):
        r = self.w.client.get('/api/maintenance/fleet-status')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['vehicles_evaluated'], 0)
        self.assertEqual(r.data['vehicles_without_records'], 1)
        self.assertEqual(r.data['vehicles'], [])

    def test_evaluates_vehicles_with_records(self):
        vid = str(self.w.vehicle.pk)
        req = self.w.post('/api/maintenance/requirements', {
            'vehicle_id': vid, 'name': 'Oil', 'interval_days': 30,
            'warning_days': 5, 'source_id': str(self.manual.pk)}).data
        self.w.post('/api/maintenance/events', {
            'vehicle_id': vid, 'event_type': 'scheduled_service',
            'occurred_on': str(date.today() - timedelta(days=40)),
            'requirement_id': req['id'], 'origin': 'organization_provided'})
        self.w.post('/api/maintenance/events', {
            'vehicle_id': vid, 'event_type': 'fault_report',
            'occurred_on': str(date.today()), 'fault_code': 'P0001',
            'origin': 'organization_provided'})
        maint = make_vehicle(self.w.org, 'WS0001', status='under_maintenance')
        ev = make_vehicle(self.w.org, 'EV0002', fuel_type='ev',
                          battery_capacity_kwh='60')
        self.w.observe('battery_soc_pct', 40, '%', vehicle_id=str(ev.pk),
                       observed_at=timezone.now().isoformat())
        r = self.w.client.get('/api/maintenance/fleet-status').data
        self.assertEqual(r['vehicles_evaluated'], 2)
        self.assertEqual(r['vehicles_without_records'], 1)  # WS0001
        self.assertEqual(r['totals']['overdue'], 1)
        self.assertEqual(r['totals']['open_faults'], 1)
        first = r['vehicles'][0]
        self.assertEqual(first['vehicle_id'], vid)
        self.assertEqual(first['worst_service_status'], 'overdue')
        self.assertEqual(first['requirements'][0]['requirement_id'],
                         req['id'])
        self.assertEqual(first['requirements'][0]['status'], 'overdue')
        self.assertIsNotNone(first['requirements'][0]['due_date'])
        self.assertEqual(first['requirements'][0]['last_service_on'],
                         str(date.today() - timedelta(days=40)))
        ev_row = next(x for x in r['vehicles']
                      if x['registration_number'] == 'EV0002')
        self.assertIsNone(ev_row['battery_soh'])  # SoC is not SoH
        self.assertNotIn(str(maint.pk), [x['vehicle_id']
                                         for x in r['vehicles']])

    def test_other_org_isolated(self):
        other = World()
        self.w.post('/api/maintenance/events', {
            'vehicle_id': str(self.w.vehicle.pk), 'event_type': 'repair',
            'occurred_on': str(date.today()),
            'origin': 'organization_provided'})
        r = other.client.get('/api/maintenance/fleet-status').data
        self.assertEqual(r['vehicles_evaluated'], 0)


class AssessmentHeadlineTests(TestCase):
    def test_summary_carries_stored_outcome(self):
        w = EvidenceWorld()
        a = w.post('/api/ev-migration/assessments/readiness', {
            'vehicle_id': str(w.vehicle.pk),
            'ev_candidate_id': str(w.candidate.pk), 'reserve_fraction': 0.2,
            'charging_connector': 'ac',
            'design_day_distance': {'value': 100, 'unit': 'km',
                                    'origin': 'assumed'}})
        self.assertEqual(a.status_code, 201, a.content)
        rows = w.client.get('/api/evidence/assessments?kind=ev_readiness').data
        self.assertEqual(rows[0]['headline']['outcome'],
                         a.data['result']['outcome'])
        self.assertEqual(rows[0]['headline']['checks']['charging'],
                         'insufficient')
