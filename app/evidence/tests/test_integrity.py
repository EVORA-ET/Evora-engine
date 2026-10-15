"""Corrections from the post-implementation review: deletion protection,
duplicate records, rejected sources, verification history, tenant isolation."""

from datetime import date

from django.test import TestCase
from django.utils import timezone

from common.testing import World, make_depot
from evidence import refresh
from evidence.models import Assessment, Observation, SourceVerification
from evidence.tests.fixtures import EvidenceWorld, make_source
from organizations.models import Organization


class DeletionProtectionTests(TestCase):
    def setUp(self):
        self.w = EvidenceWorld()
        self.vid = str(self.w.vehicle.pk)

    def test_vehicle_with_evidence_is_not_deleted(self):
        self.w.observe('odometer_km', 1000, 'km', vehicle_id=self.vid,
                       observed_at=timezone.now().isoformat())
        r = self.w.client.delete(f'/api/vehicle/{self.vid}')
        self.assertEqual(r.status_code, 409)
        self.assertIn('evidence_observations', r.data['blockers'])
        self.assertEqual(Observation.objects.count(), 1)

    def test_vehicle_with_only_an_assessment_is_not_deleted(self):
        self.w.post('/api/maintenance/assessments', {'vehicle_id': self.vid})
        r = self.w.client.delete(f'/api/vehicle/{self.vid}')
        self.assertEqual(r.status_code, 409)
        self.assertEqual(Assessment.objects.count(), 1)

    def test_vehicle_without_evidence_still_deletes(self):
        r = self.w.client.delete(f'/api/vehicle/{self.vid}')
        self.assertEqual(r.status_code, 204)

    def test_depot_with_evidence_is_not_deleted(self):
        depot = make_depot(self.w.org, 'Spare depot')
        self.w.org_value('charging_window_hours', 8, 'h',
                         origin='organization_provided',
                         depot_id=str(depot.pk))
        r = self.w.client.delete(f'/api/depot/{depot.pk}')
        self.assertEqual(r.status_code, 409)
        plain = make_depot(self.w.org, 'Empty depot')
        self.assertEqual(
            self.w.client.delete(f'/api/depot/{plain.pk}').status_code, 204)

    def test_deleting_an_organization_still_cascades(self):
        self.w.observe('odometer_km', 1000, 'km', vehicle_id=self.vid,
                       observed_at=timezone.now().isoformat())
        self.w.post('/api/maintenance/assessments', {'vehicle_id': self.vid})
        Organization.objects.filter(pk=self.w.org.pk).delete()
        self.assertEqual(Observation.objects.count(), 0)
        self.assertEqual(Assessment.objects.count(), 0)


class DuplicateTests(TestCase):
    def setUp(self):
        self.w = EvidenceWorld()
        self.vid = str(self.w.vehicle.pk)

    def test_duplicate_observation_refused_correction_allowed(self):
        t = timezone.now().isoformat()
        first = self.w.observe('odometer_km', 1000, 'km', vehicle_id=self.vid,
                               observed_at=t)
        r = self.w.observe('odometer_km', 1000, 'km', vehicle_id=self.vid,
                           observed_at=t, expect=400)
        self.assertIn('supersede', str(r))
        fix = self.w.post(
            f'/api/evidence/observations/{first["id"]}/supersede',
            {'kind': 'odometer_km', 'value': 1001, 'unit': 'km',
             'origin': 'measured', 'source_id': str(self.w.record_source.pk),
             'observed_at': t, 'vehicle_id': self.vid,
             'correction_reason': 'Misread'})
        self.assertEqual(fix.status_code, 201, fix.content)

    def test_duplicate_tariff_same_date_refused(self):
        self.w.org_value('electricity_tariff_per_kwh', 8, 'INR/kWh',
                         origin='organization_provided')
        self.w.org_value('electricity_tariff_per_kwh', 9, 'INR/kWh',
                         origin='organization_provided', expect=400)
        # a different effective date is a new tariff, not a duplicate
        self.w.org_value('electricity_tariff_per_kwh', 9, 'INR/kWh',
                         origin='organization_provided',
                         effective_from=str(date.today()))

    def test_duplicate_energy_record_refused(self):
        body = {'vehicle_id': self.vid, 'record_type': 'fuel_fill',
                'occurred_at': timezone.now().isoformat(), 'quantity': 40,
                'is_full': True, 'odometer_km': 5000,
                'origin': 'organization_provided'}
        first = self.w.post('/api/evidence/energy-records', body)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(
            self.w.post('/api/evidence/energy-records', body).status_code, 400)
        self.w.post(f'/api/evidence/energy-records/{first.data["id"]}/void',
                    {'reason': 'wrong litres'})
        self.assertEqual(
            self.w.post('/api/evidence/energy-records', body).status_code, 201)


class SourceVerificationTests(TestCase):
    def setUp(self):
        self.w = EvidenceWorld()

    def test_history_is_kept(self):
        s = make_source(self.w.org, 'Tariff order')
        for status_ in ('verified', 'rejected'):
            r = self.w.post(f'/api/evidence/sources/{s.pk}/verify',
                            {'status': status_, 'note': f'Checked: {status_}'})
            self.assertEqual(r.status_code, 200)
        r = self.w.client.get(f'/api/evidence/sources/{s.pk}')
        self.assertEqual([h['status'] for h in r.data['verification_history']],
                         ['verified', 'rejected'])
        self.assertEqual(r.data['verification_status'], 'rejected')

    def test_refresh_reset_is_recorded(self):
        s = make_source(self.w.org, 'Tariff API', verified=True,
                        retrieval_method='api', url='https://example.invalid')
        refresh.refresh_source(s, lambda src: [{
            'kind': 'electricity_tariff_per_kwh', 'value': 9.0,
            'unit': 'INR/kWh', 'effective_from': date.today()}])
        h = SourceVerification.objects.get(source=s)
        self.assertEqual(h.status, 'unverified')
        self.assertIn('Reset by refresh', h.note)

    def test_rejected_source_cannot_inform_assessment(self):
        s = make_source(self.w.org, 'Bad quote')
        o = self.w.org_value('charging_window_hours', 10, 'h',
                             origin='organization_provided', source=s,
                             depot_id=str(self.w.depot_a.pk))
        self.w.post(f'/api/evidence/sources/{s.pk}/verify',
                    {'status': 'rejected', 'note': 'Not our depot'})
        r = self.w.post('/api/ev-migration/assessments/readiness', {
            'vehicle_id': str(self.w.vehicle.pk),
            'ev_candidate_id': str(self.w.candidate.pk),
            'reserve_fraction': 0.2, 'charging_connector': 'ac',
            'design_day_distance': {'value': 100, 'unit': 'km',
                                    'origin': 'assumed'},
            'inputs': {'charging_window_hours': {'observation_id': o['id']}}})
        self.assertEqual(r.status_code, 400)
        self.assertIn('rejected', str(r.data))


class TenantIsolationTests(TestCase):
    def setUp(self):
        self.w = EvidenceWorld()
        self.other = World()
        a = self.w.post('/api/maintenance/assessments',
                        {'vehicle_id': str(self.w.vehicle.pk)})
        self.aid = a.data['id']

    def test_other_org_cannot_read_or_reproduce_assessments(self):
        c = self.other.client
        self.assertEqual(c.get(f'/api/evidence/assessments/{self.aid}')
                         .status_code, 404)
        self.assertEqual(c.post(f'/api/evidence/assessments/{self.aid}/'
                                'reproduce', {}, format='json').status_code,
                         404)
        self.assertEqual(c.get('/api/evidence/assessments').data, [])

    def test_other_org_cannot_use_foreign_evidence_or_vehicles(self):
        o = self.w.org_value('charging_window_hours', 10, 'h',
                             origin='organization_provided',
                             depot_id=str(self.w.depot_a.pk))
        other_vehicle = self.other.vehicle
        r = self.other.client.post(
            '/api/maintenance/assessments',
            {'vehicle_id': str(self.w.vehicle.pk)}, format='json')
        self.assertEqual(r.status_code, 400)
        r = self.other.client.post(
            '/api/ev-migration/assessments/readiness', {
                'vehicle_id': str(other_vehicle.pk),
                'ev_candidate_id': str(self.w.candidate.pk),
                'reserve_fraction': 0.2, 'charging_connector': 'ac',
                'design_day_distance': {'basis': 'measured_p90'},
                'inputs': {'charging_window_hours':
                           {'observation_id': o['id']}}}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('ev_candidate_id', r.data)
        self.assertEqual(self.other.client.get(
            f'/api/evidence/observations/{o["id"]}').status_code, 404)
        self.assertEqual(self.other.client.get(
            f'/api/maintenance/vehicles/{self.w.vehicle.pk}/status')
            .status_code, 404)
