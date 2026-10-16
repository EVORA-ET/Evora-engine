from datetime import date, timedelta

from django.test import TestCase

from common.testing import World, make_driver, make_job, make_vehicle


class DriverApiTests(TestCase):
    def setUp(self):
        self.w = World()

    def payload(self, **kw):
        base = dict(
            driver_code='D-100',
            name='Ravi Kumar',
            date_of_birth='1988-05-04',
            license_number='DL-555',
            license_class='HMV',
            license_expiry=(date.today() + timedelta(days=400)).isoformat(),
            experience_years='7.5',
            shift='morning',
        )
        base.update(kw)
        return base

    def test_create_and_validation(self):
        r = self.w.client.post('/api/driver', self.payload(), format='json')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.data['open_job_count'], 0)
        for bad, field in (
            (
                {
                    'date_of_birth': (
                        date.today() - timedelta(days=365 * 10)
                    ).isoformat()
                },
                'date_of_birth',
            ),
            (
                {
                    'date_of_birth': (
                        date.today() + timedelta(days=1)
                    ).isoformat()
                },
                'date_of_birth',
            ),
            ({'experience_years': '-1'}, 'experience_years'),
            (
                {'driver_code': 'D-100', 'license_number': 'DL-556'},
                'driver_code',
            ),
            (
                {'driver_code': 'D-101', 'license_expiry': '1980-01-01'},
                'license_expiry',
            ),
        ):
            r = self.w.client.post(
                '/api/driver',
                self.payload(
                    **{'driver_code': 'D-9', 'license_number': 'L-9', **bad}
                ),
                format='json',
            )
            self.assertEqual(r.status_code, 400, bad)
            self.assertIn(field, r.data, bad)

    def test_cannot_link_user_from_other_org(self):
        other = World()
        r = self.w.client.post(
            '/api/driver',
            self.payload(user_id=str(other.user.pk)),
            format='json',
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn('user_id', r.data)

    def test_filters_and_availability(self):
        make_driver(self.w.org, 'On Leave', status='on_leave')
        make_driver(
            self.w.org,
            'Expired',
            license_expiry=date.today() - timedelta(days=5),
        )
        busy = make_driver(self.w.org, 'Busy')
        make_job(
            self.w.org,
            self.w.template,
            driver=busy,
            vehicle=make_vehicle(self.w.org),
            status='ASSIGNED',
        )
        from common.testing import at

        day = at(9).date().isoformat()

        def names(q):
            r = self.w.client.get(f'/api/driver?page=1&date={day}&tz=UTC&{q}')
            self.assertEqual(r.status_code, 200, r.content)
            return {x['name'] for x in r.data['results']}

        self.assertEqual(
            names('availability=unavailable'), {'On Leave', 'Expired'}
        )
        self.assertEqual(names('availability=assigned'), {'Busy'})
        self.assertEqual(names('availability=available'), {'Asha Rao'})
        self.assertEqual(names('status=on_leave'), {'On Leave'})
        self.assertEqual(names('search=asha'), {'Asha Rao'})

    def test_delete_guard(self):
        make_job(
            self.w.org,
            self.w.template,
            driver=self.w.driver,
            status='CANCELLED',
        )
        self.assertEqual(
            self.w.client.delete(
                f'/api/driver/{self.w.driver.pk}'
            ).status_code,
            409,
        )
        spare = make_driver(self.w.org)
        self.assertEqual(
            self.w.client.delete(f'/api/driver/{spare.pk}').status_code, 204
        )
