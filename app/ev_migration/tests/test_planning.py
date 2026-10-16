"""Migration plans: persistence, validation, status rules and isolation."""

from datetime import date, datetime, timedelta, timezone as dt_tz
from unittest import mock

from django.test import TestCase

from common.testing import World, make_vehicle
from ev_migration.models import MigrationPlanItem, PlanItemStatusChange
from ev_migration.tests.test_api import MigrationApiBase

PLANS = '/api/ev-migration/plans'


class PlanTests(MigrationApiBase):
    def plan(self, **kw):
        body = {'name': 'FY27 transition', 'status': 'active',
                'budget_amount': '5000000', 'assumptions': 'Real 8% rate'}
        body.update(kw)
        r = self.w.post(PLANS, body)
        self.assertEqual(r.status_code, 201, r.content)
        return r.data

    def add(self, plan_id, **kw):
        body = {'vehicle_id': self.vid, 'ev_candidate_id': self.cid,
                'phase': 1, 'target_date': str(date.today() + timedelta(30))}
        body.update(kw)
        return self.w.post(f'{PLANS}/{plan_id}/items', body)

    def status(self, item_id, **kw):
        return self.w.post(f'/api/ev-migration/plan-items/{item_id}/status',
                           kw)

    def approve(self):
        r = self.readiness()
        d = self.w.post('/api/ev-migration/decisions', {
            'vehicle_id': self.vid, 'ev_candidate_id': self.cid,
            'decision': 'approve_transition',
            'readiness_assessment_id': r.data['id'], 'rationale': 'Board'})
        self.assertEqual(d.status_code, 201, d.content)
        return r.data, d.data

    def test_completion_date_uses_org_local_date(self):
        item = self.add(self.plan()['id']).data
        readiness, decision = self.approve()
        self.w.client.patch(
            f'/api/ev-migration/plan-items/{item["id"]}',
            {'decision_id': decision['id']}, format='json')
        self.assertEqual(self.status(item['id'], status='in_progress')
                         .status_code, 200)
        # 20:00 UTC on the 15th is already the 16th in Asia/Kolkata.
        now = datetime(2026, 10, 15, 20, 0, tzinfo=dt_tz.utc)
        with mock.patch('django.utils.timezone.now', return_value=now):
            late = self.status(item['id'], status='completed',
                               completed_on='2026-10-17', note='RC')
            self.assertEqual(late.status_code, 400)
            ok = self.status(item['id'], status='completed',
                             completed_on='2026-10-16', note='RC')
            self.assertEqual(ok.status_code, 200, ok.content)

    def test_plan_create_currency_and_summary(self):
        p = self.plan()
        self.assertEqual(p['currency'], 'INR')
        self.assertEqual(p['summary']['item_count'], 0)
        self.assertIsNone(p['summary']['completed_fraction'])
        self.assertIsNone(p['summary']['estimated_cost_total'])

    def test_item_flags_without_evidence(self):
        p = self.plan()
        r = self.add(p['id'])
        self.assertEqual(r.status_code, 201, r.content)
        codes = {f['code'] for f in r.data['flags']}
        self.assertTrue({'no_readiness', 'no_tco', 'no_approval'} <= codes)
        self.assertEqual(r.data['status'], 'planned')
        self.assertEqual(r.data['status_history'][0]['to_status'], 'planned')

    def test_failed_readiness_is_flagged(self):
        a = self.readiness(inputs={'required_payload_kg': {
            'value': 5000, 'unit': 'kg', 'origin': 'organization_provided'}})
        self.assertEqual(a.data['result']['outcome'], 'not_suitable')
        r = self.add(self.plan()['id'], readiness_assessment_id=a.data['id'])
        codes = {f['code'] for f in r.data['flags']}
        self.assertIn('readiness_failed', codes)

    def test_links_must_match_vehicle(self):
        other = make_vehicle(self.w.org, 'OTHER1')
        a = self.readiness()
        r = self.add(self.plan()['id'], vehicle_id=str(other.pk),
                     readiness_assessment_id=a.data['id'])
        self.assertEqual(r.status_code, 400)
        self.assertIn('readiness_assessment_id', r.data)

    def test_ev_vehicle_and_duplicate_rejected(self):
        p = self.plan()
        self.assertEqual(self.add(p['id']).status_code, 201)
        self.assertEqual(self.add(p['id']).status_code, 400)
        ev = make_vehicle(self.w.org, 'EV0001', fuel_type='ev')
        self.assertEqual(self.add(p['id'], vehicle_id=str(ev.pk)).status_code,
                         400)

    def test_start_requires_approval_and_completion_is_explicit(self):
        item = self.add(self.plan()['id']).data
        r = self.status(item['id'], status='in_progress')
        self.assertEqual(r.status_code, 400)
        self.assertIn('approval', str(r.data['status']))
        readiness, decision = self.approve()
        upd = self.w.client.patch(
            f'/api/ev-migration/plan-items/{item["id"]}',
            {'decision_id': decision['id'],
             'readiness_assessment_id': readiness['id']}, format='json')
        self.assertEqual(upd.status_code, 200, upd.content)
        # Approval alone does not move the item.
        self.assertEqual(upd.data['status'], 'planned')
        self.assertEqual(self.status(item['id'], status='completed')
                         .status_code, 400)  # planned -> completed not allowed
        r = self.status(item['id'], status='in_progress', note='PO raised')
        self.assertEqual(r.status_code, 200, r.content)
        r = self.status(item['id'], status='completed')
        self.assertEqual(r.status_code, 400)  # needs date and note
        r = self.status(item['id'], status='completed',
                        completed_on=str(date.today()),
                        note='Registered RC on file')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data['completed_on'], str(date.today()))
        self.assertEqual([h['to_status'] for h in r.data['status_history']],
                         ['planned', 'in_progress', 'completed'])
        self.assertEqual(self.status(item['id'], status='planned')
                         .status_code, 400)  # final

    def test_summary_progress_from_statuses(self):
        p = self.plan()
        a = self.add(p['id'], estimated_cost='1500000').data
        other = make_vehicle(self.w.org, 'OTHER2')
        b = self.add(p['id'], vehicle_id=str(other.pk), phase=2).data
        self.status(b['id'], status='cancelled', note='Sold instead')
        got = self.w.client.get(f'{PLANS}/{p["id"]}').data
        s = got['summary']
        self.assertEqual(s['by_status']['planned'], 1)
        self.assertEqual(s['by_status']['cancelled'], 1)
        self.assertEqual(s['completed_fraction'], 0.0)
        self.assertEqual(s['estimated_cost_total'], '1500000.00')
        self.assertEqual(s['items_without_cost'], 0)
        self.assertEqual(len(got['items']), 2)
        self.assertEqual(a['estimated_cost'], '1500000.00')

    def test_remove_only_untouched_items(self):
        p = self.plan()
        item = self.add(p['id']).data
        self.status(item['id'], status='deferred', note='Budget')
        r = self.w.client.delete(f'/api/ev-migration/plan-items/{item["id"]}')
        self.assertEqual(r.status_code, 409)
        other = make_vehicle(self.w.org, 'OTHER3')
        fresh = self.add(p['id'], vehicle_id=str(other.pk)).data
        r = self.w.client.delete(f'/api/ev-migration/plan-items/{fresh["id"]}')
        self.assertEqual(r.status_code, 204)
        self.assertFalse(PlanItemStatusChange.objects.filter(
            item_id=fresh['id']).exists())

    def test_completed_item_locked(self):
        readiness, decision = self.approve()
        item = self.add(self.plan()['id'], decision_id=decision['id']).data
        self.status(item['id'], status='in_progress')
        self.status(item['id'], status='completed',
                    completed_on=str(date.today()), note='Delivered')
        r = self.w.client.patch(f'/api/ev-migration/plan-items/{item["id"]}',
                                {'phase': 3}, format='json')
        self.assertEqual(r.status_code, 400)
        r = self.w.client.patch(f'/api/ev-migration/plan-items/{item["id"]}',
                                {'notes': 'Handover done'}, format='json')
        self.assertEqual(r.status_code, 200)

    def test_vehicle_in_plan_cannot_be_deleted(self):
        self.add(self.plan()['id'])
        r = self.w.client.delete(f'/api/vehicle/{self.vid}')
        self.assertEqual(r.status_code, 409)
        self.assertEqual(MigrationPlanItem.objects.count(), 1)


class PlanIsolationTests(TestCase):
    def test_other_org_cannot_see_or_touch_plans(self):
        a, b = World(), World()
        p = a.client.post(PLANS, {'name': 'A plan'}, format='json').data
        self.assertEqual(b.client.get(PLANS).data, [])
        self.assertEqual(b.client.get(f'{PLANS}/{p["id"]}').status_code, 404)
        r = b.client.post(f'{PLANS}/{p["id"]}/items',
                          {'vehicle_id': str(b.vehicle.pk), 'phase': 1},
                          format='json')
        self.assertEqual(r.status_code, 404)
        r = a.client.post(f'{PLANS}/{p["id"]}/items',
                          {'vehicle_id': str(b.vehicle.pk), 'phase': 1},
                          format='json')
        self.assertEqual(r.status_code, 400)
