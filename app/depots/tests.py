from django.test import TestCase

from common.testing import World, make_depot, make_template, make_vehicle
from fleets.models import Fleet


class DepotApiTests(TestCase):
    def setUp(self):
        self.w = World()

    def test_list_stats_are_exact(self):
        fleet = Fleet.objects.create(organization=self.w.org, name='F1')
        make_vehicle(self.w.org, depot=self.w.depot_a, fleet=fleet)
        make_vehicle(self.w.org, depot=self.w.depot_a, fuel_type='ev')
        loop = make_template(
            self.w.org, self.w.depot_a, self.w.depot_a, name='Loop'
        )
        self.assertIsNotNone(loop)
        r = self.w.client.get('/api/depot?page=1&ordering=name')
        rows = {x['name']: x for x in r.data['results']}
        a = rows['Depot A']
        self.assertEqual(
            (a['vehicle_count'], a['ev_count'], a['fleet_count']), (2, 1, 1)
        )
        self.assertEqual(
            a['route_count'], 2
        )  # 'A to B' and the Loop; loop counted once
        self.assertEqual(rows['Depot B']['route_count'], 1)

    def test_search_and_filter(self):
        make_depot(self.w.org, 'Pune Hub', charging_available=True)
        r = self.w.client.get('/api/depot?page=1&search=pune')
        self.assertEqual([x['name'] for x in r.data['results']], ['Pune Hub'])
        r = self.w.client.get('/api/depot?page=1&charging=true')
        self.assertEqual([x['name'] for x in r.data['results']], ['Pune Hub'])

    def test_delete_blocked_by_routes_and_vehicles_then_allowed(self):
        r = self.w.client.delete(f'/api/depot/{self.w.depot_a.pk}')
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.data['blockers']['routes'], 1)
        self.w.template.delete()
        v = make_vehicle(self.w.org, depot=self.w.depot_a)
        r = self.w.client.delete(f'/api/depot/{self.w.depot_a.pk}')
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.data['blockers']['vehicles'], 1)
        v.delete()
        self.assertEqual(
            self.w.client.delete(
                f'/api/depot/{self.w.depot_a.pk}'
            ).status_code,
            204,
        )

    def test_org_isolation(self):
        other = World()
        self.assertEqual(
            self.w.client.get(f'/api/depot/{other.depot_a.pk}').status_code,
            404,
        )
        self.assertEqual(
            self.w.client.delete(f'/api/depot/{other.depot_a.pk}').status_code,
            404,
        )
