from django.test import TestCase

from common.testing import World, at, make_job, make_template


class RouteApiTests(TestCase):
    def setUp(self):
        self.w = World()

    def test_create_without_destination_and_validation(self):
        r = self.w.client.post(
            '/api/job-template',
            {'name': 'Local loop', 'source_depot_id': str(self.w.depot_a.pk)},
            format='json',
        )
        self.assertEqual(r.status_code, 201, r.content)
        self.assertIsNone(r.data['destination_depot_id'])
        r = self.w.client.post(
            '/api/job-template',
            {
                'name': 'Bad',
                'source_depot_id': str(self.w.depot_a.pk),
                'start_date': '2026-05-02',
                'end_date': '2026-05-01',
            },
            format='json',
        )
        self.assertEqual(r.status_code, 400)
        r = self.w.client.post(
            '/api/job-template',
            {
                'name': 'Rec',
                'source_depot_id': str(self.w.depot_a.pk),
                'is_recurring': True,
            },
            format='json',
        )
        self.assertIn('recurrence_rule', r.data)

    def test_cannot_use_other_orgs_depot(self):
        other = World()
        r = self.w.client.post(
            '/api/job-template',
            {'name': 'X', 'source_depot_id': str(other.depot_a.pk)},
            format='json',
        )
        self.assertEqual(r.status_code, 400)

    def test_list_counts_filters_and_delete_guard(self):
        make_job(self.w.org, self.w.template, at(9), at(10))
        make_template(
            self.w.org,
            self.w.depot_b,
            None,
            name='Paused one',
            status='PAUSED',
        )
        r = self.w.client.get('/api/job-template?page=1&ordering=name')
        rows = {x['name']: x for x in r.data['results']}
        self.assertEqual(rows['A to B']['job_count'], 1)
        self.assertEqual(rows['A to B']['upcoming_job_count'], 1)
        self.assertEqual(rows['A to B']['source_depot_name'], 'Depot A')
        r = self.w.client.get('/api/job-template?page=1&status=PAUSED')
        self.assertEqual(
            [x['name'] for x in r.data['results']], ['Paused one']
        )
        r = self.w.client.delete(f'/api/job-template/{self.w.template.pk}')
        self.assertEqual(r.status_code, 409)
        self.assertTrue(
            self.w.template.job_instances.exists()
        )  # nothing cascaded
        archive = self.w.client.patch(
            f'/api/job-template/{self.w.template.pk}',
            {'status': 'ARCHIVED'},
            format='json',
        )
        self.assertEqual(archive.status_code, 200)
