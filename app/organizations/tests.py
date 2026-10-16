from django.test import TestCase

from common.testing import client_for, make_org, make_user


class OrganizationScopeTests(TestCase):
    def setUp(self):
        self.org = make_org('Mine')
        self.other = make_org('Theirs')
        self.client_ = client_for(make_user(self.org))

    def test_own_organization_readable_and_editable(self):
        r = self.client_.get(f'/api/organization/{self.org.pk}')
        self.assertEqual(r.status_code, 200)
        r = self.client_.patch(f'/api/organization/{self.org.pk}',
                               {'name': 'Mine Renamed', 'industry': 'Logistics'},
                               format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.org.refresh_from_db()
        self.assertEqual(self.org.name, 'Mine Renamed')

    def test_other_organization_is_not_found(self):
        self.assertEqual(self.client_.get(
            f'/api/organization/{self.other.pk}').status_code, 404)
        r = self.client_.patch(f'/api/organization/{self.other.pk}',
                               {'name': 'Hijacked'}, format='json')
        self.assertEqual(r.status_code, 404)
        self.other.refresh_from_db()
        self.assertEqual(self.other.name, 'Theirs')
