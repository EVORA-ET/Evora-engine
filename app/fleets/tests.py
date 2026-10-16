from django.test import TestCase

from common.testing import World, make_vehicle
from fleets.models import Fleet


class FleetApiTests(TestCase):
    def setUp(self):
        self.w = World()

    def test_crud_and_unique_name(self):
        r = self.w.client.post(
            '/api/fleet',
            {'name': 'Mumbai EV', 'description': 'x'},
            format='json',
        )
        self.assertEqual(r.status_code, 201, r.content)
        fid = r.data['id']
        self.assertEqual(
            self.w.client.post(
                '/api/fleet', {'name': 'mumbai ev'}, format='json'
            ).status_code,
            400,
        )
        self.assertEqual(
            self.w.client.post(
                '/api/fleet', {'name': '  '}, format='json'
            ).status_code,
            400,
        )
        r = self.w.client.patch(
            f'/api/fleet/{fid}', {'name': 'Mumbai Electric'}, format='json'
        )
        self.assertEqual(r.data['name'], 'Mumbai Electric')
        self.assertEqual(
            self.w.client.delete(f'/api/fleet/{fid}').status_code, 204
        )

    def test_membership_counts_depots_and_delete_keeps_vehicles(self):
        fleet = Fleet.objects.create(organization=self.w.org, name='F')
        ev = make_vehicle(self.w.org, fuel_type='ev', depot=self.w.depot_a)
        ice = make_vehicle(
            self.w.org, depot=self.w.depot_b, status='under_maintenance'
        )
        r = self.w.client.post(
            f'/api/fleet/{fleet.pk}/vehicles',
            {'add': [str(ev.pk), str(ice.pk)]},
            format='json',
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(
            (
                r.data['vehicle_count'],
                r.data['ev_count'],
                r.data['ice_count'],
                r.data['active_count'],
            ),
            (2, 1, 1, 1),
        )
        self.assertEqual(
            {d['depot_name'] for d in r.data['depots']}, {'Depot A', 'Depot B'}
        )
        self.assertEqual(
            r.data['status_counts'], {'active': 1, 'under_maintenance': 1}
        )
        r = self.w.client.post(
            f'/api/fleet/{fleet.pk}/vehicles',
            {'remove': [str(ice.pk)]},
            format='json',
        )
        self.assertEqual(r.data['vehicle_count'], 1)
        self.w.client.delete(f'/api/fleet/{fleet.pk}')
        ev.refresh_from_db()
        self.assertIsNone(ev.fleet_id)  # vehicle kept, just ungrouped

    def test_membership_is_atomic_and_org_scoped(self):
        other = World()
        fleet = Fleet.objects.create(organization=self.w.org, name='F')
        mine = make_vehicle(self.w.org)
        r = self.w.client.post(
            f'/api/fleet/{fleet.pk}/vehicles',
            {'add': [str(mine.pk), str(other.vehicle.pk)]},
            format='json',
        )
        self.assertEqual(r.status_code, 400)
        mine.refresh_from_db()
        self.assertIsNone(mine.fleet_id)  # nothing applied
        self.assertEqual(
            self.w.client.get(
                f'/api/fleet/{Fleet.objects.create(organization=other.org, name="Z").pk}'
            ).status_code,
            404,
        )

    def test_same_name_allowed_in_different_orgs(self):
        other = World()
        Fleet.objects.create(organization=other.org, name='Shared')
        self.assertEqual(
            self.w.client.post(
                '/api/fleet', {'name': 'Shared'}, format='json'
            ).status_code,
            201,
        )
