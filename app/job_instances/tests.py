import threading
from datetime import timedelta

from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from common.testing import (
    UTC,
    World,
    at,
    make_driver,
    make_job,
    make_vehicle,
)
from job_instances import services
from job_instances.models import JobInstance


def url(job, action=''):
    return f'/api/job-instance/{job.pk}' + (f'/{action}' if action else '')


class CreateJobTests(TestCase):
    def setUp(self):
        self.w = World()

    def post(self, **extra):
        body = {
            'job_template_id': str(self.w.template.pk),
            'scheduled_start_at': at(9).isoformat(),
            'scheduled_end_at': at(11).isoformat(),
            **extra,
        }
        return self.w.client.post('/api/job-instance', body, format='json')

    def test_creates_pending_unassigned_job(self):
        r = self.post()
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.data['status'], 'PENDING')
        self.assertIsNone(r.data['vehicle_id'])
        self.assertEqual(r.data['assignment_state'], 'unassigned')
        self.assertEqual(r.data['job_template_name'], 'A to B')

    def test_with_vehicle_and_driver_becomes_assigned(self):
        r = self.post(
            vehicle_id=str(self.w.vehicle.pk), driver_id=str(self.w.driver.pk)
        )
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.data['status'], 'ASSIGNED')
        self.assertEqual(r.data['vehicle_registration_number'], 'MH12AB0001')

    def test_end_defaults_from_template_duration(self):
        self.w.template.estimated_duration_minutes = 90
        self.w.template.save()
        r = self.w.client.post(
            '/api/job-instance',
            {
                'job_template_id': str(self.w.template.pk),
                'scheduled_start_at': at(9).isoformat(),
            },
            format='json',
        )
        self.assertEqual(r.status_code, 201, r.content)
        job = JobInstance.objects.get(pk=r.data['id'])
        self.assertEqual(
            job.scheduled_end_at - job.scheduled_start_at,
            timedelta(minutes=90),
        )

    def test_end_required_when_template_has_no_duration(self):
        r = self.w.client.post(
            '/api/job-instance',
            {
                'job_template_id': str(self.w.template.pk),
                'scheduled_start_at': at(9).isoformat(),
            },
            format='json',
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn('scheduled_end_at', r.data)

    def test_end_before_start_rejected(self):
        r = self.post(scheduled_end_at=at(8).isoformat())
        self.assertEqual(r.status_code, 400)

    def test_paused_route_cannot_be_scheduled(self):
        self.w.template.status = 'PAUSED'
        self.w.template.save()
        self.assertEqual(self.post().status_code, 400)

    def test_client_cannot_inject_status(self):
        r = self.post(status='COMPLETED', completion_percentage=100)
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data['status'], 'PENDING')
        self.assertEqual(r.data['completion_percentage'], 0)


class TenantIsolationTests(TestCase):
    def setUp(self):
        self.w = World()
        self.other = World()

    def test_cannot_use_other_orgs_vehicle_or_template(self):
        r = self.w.client.post(
            '/api/job-instance',
            {
                'job_template_id': str(self.w.template.pk),
                'scheduled_start_at': at(9).isoformat(),
                'scheduled_end_at': at(10).isoformat(),
                'vehicle_id': str(self.other.vehicle.pk),
            },
            format='json',
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn('vehicle_id', r.data)
        r = self.w.client.post(
            '/api/job-instance',
            {
                'job_template_id': str(self.other.template.pk),
                'scheduled_start_at': at(9).isoformat(),
                'scheduled_end_at': at(10).isoformat(),
            },
            format='json',
        )
        self.assertEqual(r.status_code, 400)

    def test_cannot_assign_other_orgs_driver(self):
        job = make_job(self.w.org, self.w.template)
        r = self.w.client.post(
            url(job, 'assign'),
            {'driver_id': str(self.other.driver.pk)},
            format='json',
        )
        self.assertEqual(r.status_code, 400)
        job.refresh_from_db()
        self.assertIsNone(job.driver_id)

    def test_cannot_read_other_orgs_job(self):
        job = make_job(self.other.org, self.other.template)
        self.assertEqual(self.w.client.get(url(job)).status_code, 404)
        self.assertEqual(
            self.w.client.post(
                url(job, 'assign'), {'driver_id': None}, format='json'
            ).status_code,
            404,
        )

    def test_vehicle_fleet_depot_fks_are_org_scoped(self):
        r = self.w.client.patch(
            f'/api/vehicle/{self.w.vehicle.pk}',
            {'depot_id': str(self.other.depot_a.pk)},
            format='json',
        )
        self.assertEqual(r.status_code, 400)


class AssignmentTests(TestCase):
    def setUp(self):
        self.w = World()
        self.job = make_job(self.w.org, self.w.template)

    def assign(self, job=None, **body):
        return self.w.client.post(
            url(job or self.job, 'assign'), body, format='json'
        )

    def test_assign_both_persists_and_sets_assigned(self):
        r = self.assign(
            vehicle_id=str(self.w.vehicle.pk), driver_id=str(self.w.driver.pk)
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.job.refresh_from_db()
        self.assertEqual(self.job.vehicle_id, self.w.vehicle.pk)
        self.assertEqual(self.job.driver_id, self.w.driver.pk)
        self.assertEqual(self.job.status, 'ASSIGNED')

    def test_partial_assignment_stays_pending(self):
        r = self.assign(vehicle_id=str(self.w.vehicle.pk))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['status'], 'PENDING')
        self.assertEqual(r.data['assignment_state'], 'missing_driver')

    def test_unassign_reverts_to_pending(self):
        self.assign(
            vehicle_id=str(self.w.vehicle.pk), driver_id=str(self.w.driver.pk)
        )
        r = self.assign(driver_id=None)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['status'], 'PENDING')
        self.assertIsNone(r.data['driver_id'])
        self.assertEqual(str(r.data['vehicle_id']), str(self.w.vehicle.pk))

    def test_overlapping_vehicle_rejected_with_reason(self):
        other = make_job(
            self.w.org,
            self.w.template,
            at(10),
            at(12),
            vehicle=self.w.vehicle,
            driver=make_driver(self.w.org),
            status='ASSIGNED',
        )
        r = self.assign(vehicle_id=str(self.w.vehicle.pk))
        self.assertEqual(r.status_code, 400)
        self.assertIn('already booked', ' '.join(r.data['vehicle_id']))
        self.job.refresh_from_db()
        self.assertIsNone(self.job.vehicle_id)
        self.assertEqual(other.status, 'ASSIGNED')

    def test_overlapping_driver_rejected(self):
        make_job(
            self.w.org,
            self.w.template,
            at(10),
            at(12),
            driver=self.w.driver,
            vehicle=make_vehicle(self.w.org),
            status='ASSIGNED',
        )
        r = self.assign(driver_id=str(self.w.driver.pk))
        self.assertEqual(r.status_code, 400)
        self.assertIn('driver_id', r.data)

    def test_back_to_back_jobs_do_not_conflict(self):
        make_job(
            self.w.org,
            self.w.template,
            at(11),
            at(13),
            vehicle=self.w.vehicle,
            status='PENDING',
        )
        self.assertEqual(
            self.assign(vehicle_id=str(self.w.vehicle.pk)).status_code, 200
        )

    def test_cancelled_and_completed_jobs_do_not_block(self):
        for st in ('CANCELLED', 'COMPLETED'):
            make_job(
                self.w.org,
                self.w.template,
                at(9),
                at(11),
                vehicle=self.w.vehicle,
                status=st,
            )
        self.assertEqual(
            self.assign(vehicle_id=str(self.w.vehicle.pk)).status_code, 200
        )

    def test_unusable_vehicle_rejected(self):
        for status in ('under_maintenance', 'inactive', 'retired', 'sold'):
            self.w.vehicle.status = status
            self.w.vehicle.save()
            r = self.assign(vehicle_id=str(self.w.vehicle.pk))
            self.assertEqual(r.status_code, 400, status)

    def test_expired_insurance_rejected(self):
        self.w.vehicle.insurance_expiry = (
            timezone.now() - timedelta(days=3)
        ).date()
        self.w.vehicle.save()
        r = self.assign(vehicle_id=str(self.w.vehicle.pk))
        self.assertEqual(r.status_code, 400)
        self.assertIn('Insurance expired', ' '.join(r.data['vehicle_id']))

    def test_unavailable_or_expired_driver_rejected(self):
        self.w.driver.status = 'on_leave'
        self.w.driver.save()
        self.assertEqual(
            self.assign(driver_id=str(self.w.driver.pk)).status_code, 400
        )
        self.w.driver.status = 'active'
        self.w.driver.license_expiry = (
            timezone.now() - timedelta(days=1)
        ).date()
        self.w.driver.save()
        r = self.assign(driver_id=str(self.w.driver.pk))
        self.assertIn('Licence expired', ' '.join(r.data['driver_id']))

    def test_unscheduled_job_cannot_be_assigned(self):
        self.job.scheduled_end_at = None
        self.job.save()
        r = self.assign(vehicle_id=str(self.w.vehicle.pk))
        self.assertEqual(r.status_code, 400)
        self.assertIn('scheduled_start_at', r.data)

    def test_terminal_job_cannot_be_assigned(self):
        self.job.status = 'COMPLETED'
        self.job.save()
        self.assertEqual(
            self.assign(vehicle_id=str(self.w.vehicle.pk)).status_code, 400
        )

    def test_reassign_moves_vehicle_and_frees_old_one(self):
        v2 = make_vehicle(self.w.org)
        self.assign(
            vehicle_id=str(self.w.vehicle.pk), driver_id=str(self.w.driver.pk)
        )
        self.assertEqual(self.assign(vehicle_id=str(v2.pk)).status_code, 200)
        self.job.refresh_from_db()
        self.assertEqual(self.job.vehicle_id, v2.pk)
        # The old vehicle is free again for an overlapping job.
        second = make_job(self.w.org, self.w.template)
        self.assertEqual(
            self.w.client.post(
                url(second, 'assign'),
                {'vehicle_id': str(self.w.vehicle.pk)},
                format='json',
            ).status_code,
            200,
        )

    def test_noop_when_nothing_changes_does_not_revalidate(self):
        self.assign(
            vehicle_id=str(self.w.vehicle.pk), driver_id=str(self.w.driver.pk)
        )
        self.w.vehicle.status = 'under_maintenance'
        self.w.vehicle.save()
        # Changing only the driver must not be blocked by the vehicle's later problem.
        d2 = make_driver(self.w.org)
        self.assertEqual(self.assign(driver_id=str(d2.pk)).status_code, 200)

    def test_empty_body_rejected(self):
        self.assertEqual(self.assign().status_code, 400)

    def test_reschedule_into_conflict_rejected(self):
        self.assign(
            vehicle_id=str(self.w.vehicle.pk), driver_id=str(self.w.driver.pk)
        )
        make_job(
            self.w.org, self.w.template, at(14), at(16), vehicle=self.w.vehicle
        )
        r = self.w.client.patch(
            url(self.job),
            {
                'scheduled_start_at': at(15).isoformat(),
                'scheduled_end_at': at(17).isoformat(),
            },
            format='json',
        )
        self.assertEqual(r.status_code, 400)
        self.job.refresh_from_db()
        self.assertEqual(self.job.scheduled_start_at, at(9))


class EligibleResourcesTests(TestCase):
    def test_flags_and_orders_candidates(self):
        w = World()
        job = make_job(w.org, w.template)
        busy = make_vehicle(w.org, 'BUSY1')
        make_job(w.org, w.template, at(9), at(10), vehicle=busy)
        make_vehicle(w.org, 'BROKEN1', status='under_maintenance')
        make_vehicle(w.org, 'GONE1', status='retired')
        make_vehicle(w.org, 'ZZ-HERE', depot=w.depot_a)
        r = w.client.get(url(job, 'eligible-resources'))
        self.assertEqual(r.status_code, 200)
        by_reg = {v['registration_number']: v for v in r.data['vehicles']}
        self.assertNotIn('GONE1', by_reg)
        self.assertFalse(by_reg['BUSY1']['eligible'])
        self.assertIn('already booked', by_reg['BUSY1']['reasons'][0].lower())
        self.assertFalse(by_reg['BROKEN1']['eligible'])
        self.assertTrue(by_reg['MH12AB0001']['eligible'])
        order = [v['registration_number'] for v in r.data['vehicles']]
        self.assertEqual(
            order[0], 'ZZ-HERE'
        )  # eligible AND at the source depot sorts first
        self.assertTrue(r.data['vehicles'][0]['at_source_depot'])

    def test_ev_energy_warning(self):
        w = World()
        w.template.estimated_energy_kwh = '80'
        w.template.save()
        make_vehicle(
            w.org, 'EV1', fuel_type='ev', battery_capacity_kwh='60'
        )
        job = make_job(w.org, w.template)
        r = w.client.get(url(job, 'eligible-resources'))
        row = next(
            v for v in r.data['vehicles'] if v['registration_number'] == 'EV1'
        )
        self.assertTrue(row['eligible'])
        self.assertTrue(row['warnings'])


class StatusTransitionTests(TestCase):
    def setUp(self):
        self.w = World()
        self.job = make_job(
            self.w.org,
            self.w.template,
            vehicle=self.w.vehicle,
            driver=self.w.driver,
            status='ASSIGNED',
        )

    def move(self, to, job=None):
        return self.w.client.post(
            url(job or self.job, 'status'), {'status': to}, format='json'
        )

    def test_happy_path_records_times(self):
        r = self.move('IN_PROGRESS')
        self.assertEqual(r.status_code, 200, r.content)
        self.job.refresh_from_db()
        self.assertIsNotNone(self.job.actual_start_at)
        r = self.move('COMPLETED')
        self.assertEqual(r.status_code, 200)
        self.job.refresh_from_db()
        self.assertEqual(self.job.completion_percentage, 100)
        self.assertIsNotNone(self.job.actual_end_at)

    def test_cannot_skip_or_reverse(self):
        pending = make_job(self.w.org, self.w.template)
        self.assertEqual(self.move('IN_PROGRESS', pending).status_code, 400)
        self.assertEqual(
            self.move('COMPLETED').status_code, 400
        )  # assigned -> completed
        self.move('IN_PROGRESS')
        self.move('COMPLETED')
        self.assertEqual(
            self.move('IN_PROGRESS').status_code, 400
        )  # completed is final
        self.assertEqual(self.move('CANCELLED').status_code, 400)

    def test_assigned_pending_via_status_endpoint_forbidden(self):
        pending = make_job(self.w.org, self.w.template)
        self.assertEqual(self.move('ASSIGNED', pending).status_code, 400)
        self.assertEqual(self.move('PENDING').status_code, 400)

    def test_cancel_frees_resources(self):
        self.assertEqual(self.move('CANCELLED').status_code, 200)
        other = make_job(self.w.org, self.w.template)
        r = self.w.client.post(
            url(other, 'assign'),
            {'vehicle_id': str(self.w.vehicle.pk)},
            format='json',
        )
        self.assertEqual(r.status_code, 200)

    def test_cannot_start_with_unavailable_vehicle(self):
        self.w.vehicle.status = 'under_maintenance'
        self.w.vehicle.save()
        self.assertEqual(self.move('IN_PROGRESS').status_code, 400)

    def test_cannot_start_if_vehicle_already_running(self):
        make_job(
            self.w.org,
            self.w.template,
            at(20),
            at(22),
            vehicle=self.w.vehicle,
            driver=make_driver(self.w.org),
            status='IN_PROGRESS',
        )
        r = self.move('IN_PROGRESS')
        self.assertEqual(r.status_code, 400)
        self.assertIn('already running', str(r.data))

    def test_status_cannot_be_patched_directly(self):
        r = self.w.client.patch(
            url(self.job), {'status': 'COMPLETED'}, format='json'
        )
        self.assertEqual(r.status_code, 400)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, 'ASSIGNED')

    def test_progress_only_while_in_progress(self):
        r = self.w.client.patch(
            url(self.job), {'completion_percentage': 40}, format='json'
        )
        self.assertEqual(r.status_code, 400)
        self.move('IN_PROGRESS')
        r = self.w.client.patch(
            url(self.job), {'completion_percentage': 40}, format='json'
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['completion_percentage'], 40)

    def test_delete_blocked_for_history(self):
        self.move('IN_PROGRESS')
        self.assertEqual(self.w.client.delete(url(self.job)).status_code, 409)
        self.move('COMPLETED')
        self.assertEqual(self.w.client.delete(url(self.job)).status_code, 409)
        pending = make_job(self.w.org, self.w.template)
        self.assertEqual(self.w.client.delete(url(pending)).status_code, 204)


class ListFilterTests(TestCase):
    def setUp(self):
        self.w = World()
        w = self.w
        self.unassigned = make_job(w.org, w.template, at(8), at(9))
        self.partial = make_job(
            w.org, w.template, at(10), at(11), vehicle=w.vehicle
        )
        self.full = make_job(
            w.org,
            w.template,
            at(12),
            at(13),
            vehicle=make_vehicle(w.org),
            driver=w.driver,
            status='ASSIGNED',
        )
        self.far = make_job(w.org, w.template, at(9, days=5), at(10, days=5))

    def ids(self, query):
        r = self.w.client.get('/api/job-instance?' + query)
        self.assertEqual(r.status_code, 200, r.content)
        rows = r.data['results'] if isinstance(r.data, dict) else r.data
        return {str(x['id']) for x in rows}

    def test_legacy_call_returns_bare_array(self):
        r = self.w.client.get('/api/job-instance')
        self.assertIsInstance(r.data, list)
        self.assertEqual(len(r.data), 4)

    def test_assignment_filters(self):
        self.assertEqual(
            self.ids('assignment=unassigned'),
            {str(self.unassigned.pk), str(self.far.pk)},
        )
        self.assertEqual(self.ids('assignment=assigned'), {str(self.full.pk)})
        self.assertEqual(
            self.ids('assignment=missing_vehicle'),
            {str(self.unassigned.pk), str(self.far.pk)},
        )
        self.assertEqual(
            self.ids('assignment=missing_driver'),
            {str(self.unassigned.pk), str(self.partial.pk), str(self.far.pk)},
        )

    def test_date_filter_uses_requested_timezone(self):
        day = at(8).date().isoformat()
        got = self.ids(f'date={day}&tz=UTC')
        self.assertEqual(
            got,
            {str(self.unassigned.pk), str(self.partial.pk), str(self.full.pk)},
        )
        # In a zone 14h ahead, the 08:00-13:00 UTC jobs fall on the same local
        # day only partly: date boundaries must follow `tz`, not the server.
        shifted = self.ids(f'date={day}&tz=Pacific/Kiritimati')
        self.assertNotEqual(got, shifted)

    def test_status_vehicle_search_and_paging(self):
        self.assertEqual(self.ids('status=ASSIGNED'), {str(self.full.pk)})
        self.assertEqual(
            self.ids(f'vehicle_id={self.w.vehicle.pk}'), {str(self.partial.pk)}
        )
        self.assertEqual(self.ids('search=MH12AB0001'), {str(self.partial.pk)})
        r = self.w.client.get(
            '/api/job-instance?page=1&page_size=2&ordering=scheduled_start_at'
        )
        self.assertEqual(r.data['count'], 4)
        self.assertEqual(len(r.data['results']), 2)
        self.assertEqual(r.data['total_pages'], 2)
        self.assertEqual(
            str(r.data['results'][0]['id']), str(self.unassigned.pk)
        )

    def test_bad_params_are_400_not_500(self):
        for q in (
            'status=NOPE',
            'assignment=x',
            'date=yesterday',
            'vehicle_id=zzz',
            'ordering=password',
            'page=0',
            'page_size=abc',
        ):
            self.assertEqual(
                self.w.client.get('/api/job-instance?' + q).status_code, 400, q
            )


class ConcurrencyTests(TransactionTestCase):
    """Two requests race for the same vehicle in overlapping windows: exactly
    one may win. Without row locks both pass the overlap check."""

    def test_only_one_of_two_concurrent_assignments_succeeds(self):
        w = World()
        job1 = make_job(w.org, w.template, at(9), at(11))
        job2 = make_job(w.org, w.template, at(10), at(12))
        barrier = threading.Barrier(2)
        results = {}

        def run(name, job):
            try:
                barrier.wait(timeout=10)
                services.assign_job(
                    job_id=job.pk, org=w.org, tz=UTC, vehicle_id=w.vehicle.pk
                )
                results[name] = 'ok'
            except Exception as exc:  # ValidationError expected for the loser
                results[name] = type(exc).__name__
            finally:
                connection.close()

        threads = [
            threading.Thread(target=run, args=(n, j))
            for n, j in (('a', job1), ('b', job2))
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(
            sorted(results.values()), ['ValidationError', 'ok'], results
        )
        self.assertEqual(
            JobInstance.objects.filter(vehicle=w.vehicle).count(), 1
        )
