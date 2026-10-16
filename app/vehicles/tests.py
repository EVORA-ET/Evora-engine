from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from common.testing import World, at, make_driver, make_job, make_vehicle
from fleets.models import Fleet


class VehicleApiTests(TestCase):
    def setUp(self):
        self.w = World()

    def payload(self, **kw):
        base = dict(
            registration_number='KA01AA1111',
            manufacturer='Mahindra',
            model='Treo',
            manufacture_year=2023,
            purchase_date='2023-02-01',
            vehicle_type='car',
            fuel_type='ev',
            mileage_kmpl='8',
            payload_kg='500',
            seating_capacity=4,
            gvw_kg='1200',
        )
        base.update(kw)
        return base

    def test_create_with_fleet_and_depot_roundtrips(self):
        fleet = Fleet.objects.create(organization=self.w.org, name='EV fleet')
        r = self.w.client.post(
            '/api/vehicle',
            self.payload(
                fleet_id=str(fleet.pk), depot_id=str(self.w.depot_a.pk)
            ),
            format='json',
        )
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.data['fleet_name'], 'EV fleet')
        self.assertEqual(r.data['depot_name'], 'Depot A')
        got = self.w.client.get(f"/api/vehicle/{r.data['id']}")
        self.assertEqual(str(got.data['depot_id']), str(self.w.depot_a.pk))
        self.assertEqual(got.data['open_job_count'], 0)

    def test_duplicate_registration_rejected_case_insensitively_per_org(self):
        self.assertEqual(
            self.w.client.post(
                '/api/vehicle', self.payload(), format='json'
            ).status_code,
            201,
        )
        r = self.w.client.post(
            '/api/vehicle',
            self.payload(registration_number=' ka01aa1111 '),
            format='json',
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn('registration_number', r.data)
        other = World()  # a different organization may reuse the number
        self.assertEqual(
            other.client.post(
                '/api/vehicle', self.payload(), format='json'
            ).status_code,
            201,
        )

    def test_editing_other_fields_does_not_trip_uniqueness(self):
        r = self.w.client.patch(
            f'/api/vehicle/{self.w.vehicle.pk}',
            {'registration_number': 'MH12AB0001', 'model': 'X'},
            format='json',
        )
        self.assertEqual(r.status_code, 200, r.content)

    def test_filters_search_and_availability(self):
        make_vehicle(
            self.w.org, 'EV-1', fuel_type='ev', depot=self.w.depot_a
        )
        make_vehicle(self.w.org, 'MX-1', status='under_maintenance')
        make_vehicle(
            self.w.org,
            'INS-1',
            insurance_expiry=(timezone.now() - timedelta(days=2)).date(),
        )
        busy = make_vehicle(self.w.org, 'BUSY-1')
        make_job(
            self.w.org,
            self.w.template,
            at(9),
            at(10),
            vehicle=busy,
            driver=make_driver(self.w.org),
            status='ASSIGNED',
        )
        day = at(9).date().isoformat()

        def regs(q):
            r = self.w.client.get(
                f'/api/vehicle?page=1&page_size=50&date={day}&tz=UTC&{q}'
            )
            self.assertEqual(r.status_code, 200, r.content)
            return {x['registration_number'] for x in r.data['results']}

        self.assertEqual(regs('powertrain=ev'), {'EV-1'})
        self.assertEqual(regs('depot_id=none') & {'EV-1'}, set())
        self.assertEqual(regs(f'depot_id={self.w.depot_a.pk}'), {'EV-1'})
        self.assertEqual(regs('search=ins-'), {'INS-1'})
        self.assertEqual(regs('availability=unavailable'), {'MX-1', 'INS-1'})
        self.assertEqual(regs('availability=assigned'), {'BUSY-1'})
        self.assertEqual(
            regs('availability=available'), {'MH12AB0001', 'EV-1'}
        )

    def test_page_enrichment_shows_current_job(self):
        make_job(
            self.w.org,
            self.w.template,
            vehicle=self.w.vehicle,
            status='ASSIGNED',
        )
        r = self.w.client.get('/api/vehicle?page=1')
        row = next(
            x for x in r.data['results'] if x['id'] == str(self.w.vehicle.pk)
        )
        self.assertEqual(row['open_job_count'], 1)
        self.assertEqual(row['current_job']['job_template_name'], 'A to B')

    def test_legacy_list_unchanged_shape(self):
        r = self.w.client.get('/api/vehicle')
        self.assertIsInstance(r.data, list)
        self.assertNotIn('current_job', r.data[0])

    def test_delete_blocked_when_used_but_allowed_when_never_used(self):
        make_job(
            self.w.org,
            self.w.template,
            vehicle=self.w.vehicle,
            status='COMPLETED',
        )
        r = self.w.client.delete(f'/api/vehicle/{self.w.vehicle.pk}')
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.data['blockers']['job_instances'], 1)
        spare = make_vehicle(self.w.org)
        self.assertEqual(
            self.w.client.delete(f'/api/vehicle/{spare.pk}').status_code, 204
        )

    def test_marking_unavailable_keeps_jobs_and_reports_them(self):
        job = make_job(
            self.w.org,
            self.w.template,
            vehicle=self.w.vehicle,
            driver=self.w.driver,
            status='ASSIGNED',
        )
        r = self.w.client.patch(
            f'/api/vehicle/{self.w.vehicle.pk}',
            {'status': 'under_maintenance'},
            format='json',
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['open_job_count'], 1)
        job.refresh_from_db()
        self.assertEqual(job.vehicle_id, self.w.vehicle.pk)
