from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from common.testing import UTC, World, at, make_driver, make_job, make_vehicle


class OverviewTests(TestCase):
    """Counts must equal what is in the database, by the documented rules."""

    def setUp(self):
        self.w = World()
        w = self.w
        self.day = at(9).date().isoformat()
        self.v_maint = make_vehicle(w.org, status='under_maintenance')
        self.v_busy = make_vehicle(w.org)
        self.d_leave = make_driver(w.org, status='on_leave')
        self.d_busy = make_driver(w.org)
        make_job(
            w.org,
            w.template,
            at(9),
            at(10),
            vehicle=self.v_busy,
            driver=self.d_busy,
            status='ASSIGNED',
        )
        make_job(w.org, w.template, at(11), at(12))  # pending
        make_job(
            w.org,
            w.template,
            at(13),
            at(14),
            vehicle=w.vehicle,
            driver=w.driver,
            status='COMPLETED',
        )
        make_job(w.org, w.template, at(15), at(16), status='CANCELLED')
        make_job(w.org, w.template, at(9, days=3), at(10, days=3))  # other day

    def get(self, name):
        r = self.w.client.get(f'/api/operations/{name}?date={self.day}&tz=UTC')
        self.assertEqual(r.status_code, 200, r.content)
        return r.data

    def test_summary_numbers(self):
        s = self.get('summary')
        # vehicles: World vehicle (usable, only on a COMPLETED job), v_busy (assigned), v_maint
        self.assertEqual(
            s['vehicles'],
            {
                **s['vehicles'],
                'total': 3,
                'available': 1,
                'assigned': 1,
                'unavailable': 1,
            },
        )
        self.assertEqual(
            s['vehicles']['available']
            + s['vehicles']['assigned']
            + s['vehicles']['unavailable'],
            s['vehicles']['total'],
        )
        self.assertEqual(
            (
                s['drivers']['total'],
                s['drivers']['available'],
                s['drivers']['assigned'],
                s['drivers']['unavailable'],
            ),
            (3, 1, 1, 1),
        )
        self.assertEqual(
            s['jobs'],
            {
                **s['jobs'],
                'total': 4,
                'pending': 1,
                'assigned': 1,
                'in_progress': 0,
                'completed': 1,
                'cancelled': 1,
            },
        )
        self.assertEqual(s['resources']['depots'], 2)
        self.assertEqual(s['resources']['routes']['total'], 1)

    def test_summary_matches_list_endpoints(self):
        s = self.get('summary')
        for kind, model in (('vehicle', 'vehicles'), ('driver', 'drivers')):
            for state in ('available', 'assigned', 'unavailable'):
                r = self.w.client.get(
                    f'/api/{kind}?page=1&availability={state}&date={self.day}&tz=UTC'
                )
                self.assertEqual(
                    r.data['count'], s[model][state], (kind, state)
                )

    def test_performance_definitions(self):
        s = self.get('summary')['performance']
        # Non-cancelled jobs on the day: assigned, pending, completed -> 2 of 3 have both resources? no: 2 of 3 = assigned+completed
        self.assertEqual(
            (
                s['assignment_coverage']['numerator'],
                s['assignment_coverage']['denominator'],
            ),
            (2, 3),
        )
        self.assertEqual(s['assignment_coverage']['rate'], 66.7)
        # utilisation: usable vehicles used that day (World vehicle + v_busy) / usable vehicles (2)
        self.assertEqual(
            (
                s['fleet_utilisation']['numerator'],
                s['fleet_utilisation']['denominator'],
            ),
            (2, 2),
        )

    def test_empty_org_has_null_rates_not_fake_zeros(self):
        from common.testing import client_for, make_org, make_user

        org = make_org('Empty')
        client = client_for(make_user(org))
        r = client.get(f'/api/operations/summary?date={self.day}&tz=UTC')
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.data['performance']['completion_rate']['rate'])
        self.assertIsNone(r.data['performance']['fleet_utilisation']['rate'])
        self.assertEqual(r.data['jobs']['total'], 0)
        a = client.get(f'/api/operations/attention?date={self.day}&tz=UTC')
        self.assertEqual(a.data['items'], [])

    def test_attention_items_are_derived_and_actionable(self):
        w = self.w
        # unusable assigned vehicle + double booking + late start
        w.vehicle.status = 'under_maintenance'
        w.vehicle.save()
        make_job(
            w.org,
            w.template,
            at(9, 30),
            at(10, 30),
            vehicle=self.v_busy,
            driver=make_driver(w.org),
            status='ASSIGNED',
        )
        a = self.get('attention')
        kinds = {(i['kind'], str(i['job_id'])) for i in a['items']}
        by_kind = {i['kind'] for i in a['items']}
        self.assertIn('unassigned_job', by_kind)
        self.assertIn('vehicle_conflict', by_kind)
        for item in a['items']:
            self.assertTrue(item['message'])
            self.assertIn(
                item['action']['type'],
                {'assign_job', 'open_vehicle', 'open_driver', 'open_job'},
            )
        self.assertEqual(
            a['jobs_flagged'],
            self.get('summary')['jobs']['requiring_attention'],
        )
        self.assertEqual(len(kinds), len(a['items']))  # ids are unique

    def test_late_start_and_overdue_detection_uses_real_clock(self):
        w = self.w
        past = timezone.now().astimezone(UTC) - timedelta(hours=3)
        job = make_job(
            w.org,
            w.template,
            past,
            past + timedelta(hours=1),
            vehicle=self.v_busy,
            driver=self.d_busy,
            status='ASSIGNED',
        )
        day = past.date().isoformat()
        r = w.client.get(f'/api/operations/attention?date={day}&tz=UTC')
        kinds = {
            i['kind']
            for i in r.data['items']
            if str(i['job_id']) == str(job.pk)
        }
        self.assertIn('late_start', kinds)

    def test_isolated_between_orgs(self):
        other = World()
        r = other.client.get(f'/api/operations/summary?date={self.day}&tz=UTC')
        self.assertEqual(r.data['vehicles']['total'], 1)
        self.assertEqual(r.data['jobs']['total'], 0)
