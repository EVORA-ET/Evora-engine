from datetime import date, timedelta

from django.test import TestCase
from django.utils import timezone

from evidence.assessments import reproduce
from evidence.models import Assessment
from evidence.tests.fixtures import EvidenceWorld, make_source
from maintenance.models import MaintenanceEvent


class MaintenanceApiTests(TestCase):
    def setUp(self):
        w = self.w = EvidenceWorld()
        self.vid = str(w.vehicle.pk)
        self.manual = make_source(w.org, 'Owner manual sec. 7', verified=True,
                                  source_type='manufacturer_document')

    def status(self):
        r = self.w.client.get(f'/api/maintenance/vehicles/{self.vid}/status')
        self.assertEqual(r.status_code, 200, r.content)
        return r.data

    def requirement(self, **kw):
        body = {'vehicle_id': self.vid, 'name': 'Engine oil',
                'interval_km': 10000, 'interval_days': 365,
                'warning_km': 1000, 'warning_days': 30,
                'source_id': str(self.manual.pk)}
        body.update(kw)
        return self.w.post('/api/maintenance/requirements', body)

    def event(self, **kw):
        body = {'vehicle_id': self.vid, 'event_type': 'scheduled_service',
                'occurred_on': str(date.today() - timedelta(days=100)),
                'origin': 'organization_provided'}
        body.update(kw)
        return self.w.post('/api/maintenance/events', body)

    def test_no_history_means_no_claims(self):
        s = self.status()
        res = s['result']
        self.assertEqual(res['service_requirements'], [])
        self.assertFalse(res['predictive_assessment']['available'])
        a = self.w.post('/api/maintenance/assessments',
                        {'vehicle_id': self.vid})
        self.assertEqual(a.status_code, 201, a.content)
        self.assertEqual(a.data['evidence_level'], 'insufficient')
        self.assertEqual(a.data['status'], 'insufficient_evidence')

    def test_requirement_needs_cited_source(self):
        r = self.requirement(source_id=None)
        self.assertEqual(r.status_code, 400)

    def test_due_soon_from_records(self):
        req = self.requirement()
        self.assertEqual(req.status_code, 201, req.content)
        ev = self.event(odometer_km=50000, requirement_id=req.data['id'])
        self.assertEqual(ev.status_code, 201, ev.content)
        self.w.observe('odometer_km', 59200, 'km', vehicle_id=self.vid,
                       observed_at=timezone.now().isoformat())
        res = self.status()['result']
        svc = res['service_requirements'][0]
        self.assertEqual(svc['status'], 'due_soon')
        self.assertEqual(svc['dimensions']['distance']['km_remaining'], 800.0)
        self.assertEqual(svc['source']['title'], 'Owner manual sec. 7')
        a = self.w.post('/api/maintenance/assessments',
                        {'vehicle_id': self.vid})
        self.assertEqual(a.data['evidence_level'], 'high')
        self.assertTrue(reproduce(Assessment.objects.get(
            pk=a.data['id']))['result_matches'])

    def test_fault_lifecycle(self):
        f = self.event(event_type='fault_report', fault_code='P0420',
                       fault_severity='medium',
                       occurred_on=str(date.today() - timedelta(days=3)))
        self.assertEqual(f.status_code, 201, f.content)
        self.assertEqual(len(self.status()['result']['open_faults']), 1)
        fix = self.event(event_type='repair', resolves_id=f.data['id'],
                         occurred_on=str(date.today()), cost=4500,
                         currency='INR', downtime_hours=6)
        self.assertEqual(fix.status_code, 201, fix.content)
        res = self.status()['result']
        self.assertEqual(res['open_faults'], [])
        self.assertEqual(res['records']['recorded_cost_by_currency'],
                         {'INR': 4500.0})

    def test_status_flag_is_not_a_fault(self):
        self.w.vehicle.status = 'under_maintenance'
        self.w.vehicle.save(update_fields=['status'])
        res = self.status()['result']
        self.assertEqual(res['open_faults'], [])
        self.assertTrue(any('not, by itself, evidence of a fault' in m
                            for m in res['messages']))

    def test_soc_does_not_feed_battery_health(self):
        self.w.vehicle.fuel_type = 'ev'
        self.w.vehicle.battery_capacity_kwh = 60
        self.w.vehicle.save(update_fields=['fuel_type', 'battery_capacity_kwh'])
        self.w.observe('battery_soc_pct', 12, '%', vehicle_id=self.vid,
                       observed_at=timezone.now().isoformat())
        battery = self.status()['result']['battery']
        self.assertIsNone(battery['state_of_health'])
        self.assertEqual(battery['state_of_charge']['pct'], 12.0)
        self.w.observe('battery_soh_pct', 88, '%', vehicle_id=self.vid,
                       observed_at=timezone.now().isoformat(),
                       qualifiers={'method': 'bms_reported'})
        battery = self.status()['result']['battery']
        self.assertEqual(battery['state_of_health']['pct'], 88.0)
        self.assertEqual(battery['state_of_charge']['pct'], 12.0)

    def test_void_event(self):
        e = self.event(event_type='repair')
        r = self.w.post(f'/api/maintenance/events/{e.data["id"]}/void',
                        {'reason': 'Wrong vehicle'})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(MaintenanceEvent.objects.filter(
            pk=e.data['id']).exists())
        self.assertEqual(self.status()['result']['records']['event_count'], 0)

    def test_event_validation(self):
        self.assertEqual(self.event(occurred_on=str(
            date.today() + timedelta(days=2))).status_code, 400)
        self.assertEqual(self.event(event_type='fault_report').status_code,
                         400)
        self.assertEqual(self.event(origin='assumed').status_code, 400)
