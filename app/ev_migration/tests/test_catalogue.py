"""EV catalogue: cited seed, automatic candidates, overview and costs."""

from datetime import date, timedelta
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.utils import timezone

from common.testing import World, make_vehicle
from evidence.models import EnergyRecord, Observation
from ev_migration.models import EVCandidate, EVModel
from ev_migration.tests.test_api import MigrationApiBase
from maintenance.models import MaintenanceEvent

CAT = '/api/ev-migration/catalogue'


def zeo():
    return EVModel.objects.get(manufacturer='Mahindra', model='ZEO',
                               variant='V2 Flat Side Deck')


class CatalogueTests(MigrationApiBase):
    def use(self, m):
        return self.w.post(f'{CAT}/{m.pk}/candidate', {})

    def test_seed_is_cited_and_lists_gaps(self):
        r = self.w.client.get(CAT)
        self.assertEqual(r.status_code, 200)
        self.assertGreaterEqual(len(r.data), 5)
        for row in r.data:
            self.assertTrue(row['source_url'].startswith('https://'))
            self.assertEqual(row['verification_status'], 'unverified')
        eicher = next(x for x in r.data if x['model'] == 'Pro 2055 EV')
        self.assertFalse(eicher['assessable'])
        self.assertEqual(eicher['missing_specs'], ['payload_kg'])
        vans = self.w.client.get(f'{CAT}?vehicle_type=van').data
        self.assertTrue(vans and all(x['vehicle_type'] == 'van' for x in vans))

    def test_unassessable_model_is_refused(self):
        m = EVModel.objects.get(model='Pro 2055 EV')
        r = self.use(m)
        self.assertEqual(r.status_code, 400)
        self.assertIn('payload', str(r.data))

    def test_candidate_created_once_with_provenance_and_price(self):
        r = self.use(zeo())
        self.assertEqual(r.status_code, 200, r.content)
        c = EVCandidate.objects.get(pk=r.data['id'])
        self.assertEqual(c.origin, 'modelled')
        self.assertEqual(c.consumption_source_type, 'derived_from_rated_range')
        self.assertEqual(c.energy_consumption_kwh_per_km, Decimal('0.1331'))
        self.assertEqual(c.source.identifier, f'evora-catalogue:{zeo().pk}')
        self.assertEqual(c.source.verification_status, 'unverified')
        price = Observation.objects.get(ev_candidate=c,
                                        kind='vehicle_purchase_price')
        self.assertEqual(price.qualifiers, {'purpose': 'published_list_price'})
        self.assertEqual(price.value, Decimal('769000'))
        self.assertEqual(self.use(zeo()).data['id'], r.data['id'])
        # Organization users cannot verify catalogue sources themselves.
        v = self.w.post(f'/api/evidence/sources/{c.source_id}/verify',
                        {'status': 'verified', 'note': 'looks fine'})
        self.assertEqual(v.status_code, 400)

    def test_staff_verification_creates_fresh_verified_copy(self):
        first = self.use(zeo()).data['id']
        call_command('verify_ev_catalogue_model', str(zeo().pk),
                     by='QA staff', note='Checked press release',
                     stdout=StringIO())
        second = self.use(zeo()).data
        self.assertNotEqual(first, second['id'])
        c = EVCandidate.objects.get(pk=second['id'])
        self.assertEqual(c.source.verification_status, 'verified')
        self.assertEqual(EVCandidate.objects.get(pk=first).source
                         .verification_status, 'unverified')

    def test_assessments_run_on_catalogue_candidate(self):
        cid = self.use(zeo()).data['id']
        r = self.readiness(ev_candidate_id=cid)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertTrue(any('derived from the published range' in w
                            for w in r.data['result']['warnings']))
        self.add_costs()
        t = self.tco(ev_candidate_id=cid)
        self.assertEqual(t.status_code, 201, t.content)
        price = t.data['input_snapshot']['inputs']['ev_purchase_price']
        self.assertEqual(price['value'], 769000.0)
        self.assertEqual(price['qualifiers'],
                         {'purpose': 'published_list_price'})


class OverviewTests(MigrationApiBase):
    def test_composition_and_states(self):
        org = self.w.org
        make_vehicle(org, 'EV1', fuel_type='ev')
        make_vehicle(org, 'OTH1', fuel_type='other')
        make_vehicle(org, 'SOLD1', fuel_type='diesel', status='sold')
        self.readiness()
        o = self.w.client.get('/api/ev-migration/overview').data
        comp = o['composition']
        self.assertEqual(comp['ev'], 1)
        self.assertEqual(comp['unclassified'], 1)
        self.assertEqual(comp['total'], comp['ev'] + comp['ice'] + 1)
        self.assertEqual(comp['ev_share_pct'],
                         round(100 / (comp['ev'] + comp['ice']), 1))
        rd = o['ice_readiness']
        self.assertEqual(rd['vehicles'], comp['ice'])
        self.assertEqual(rd['meets_requirements'], 1)
        self.assertEqual(rd['not_assessed'], comp['ice'] - 1)
        states = self.w.client.get('/api/ev-migration/vehicle-states').data
        self.assertEqual(states[self.vid]['state'], 'meets_requirements')
        # Other organizations see none of it.
        other = World()
        o2 = other.client.get('/api/ev-migration/overview').data
        self.assertEqual(o2['ice_readiness']['meets_requirements'], 0)

    def test_plan_target_validation(self):
        r = self.w.post('/api/ev-migration/plans',
                        {'name': 'T', 'target_ev_share_pct': '120'})
        self.assertEqual(r.status_code, 400)
        r = self.w.post('/api/ev-migration/plans',
                        {'name': 'T', 'status': 'active',
                         'target_ev_share_pct': '50',
                         'end_date': str(date.today() + timedelta(365))})
        self.assertEqual(r.status_code, 201, r.content)
        t = self.w.client.get('/api/ev-migration/overview').data['target']
        self.assertEqual(t['ev_share_pct'], 50.0)


class RecordedCostTests(MigrationApiBase):
    def test_missing_costs_are_not_zero(self):
        org, v = self.w.org, self.w.vehicle
        now = timezone.now()
        EnergyRecord.objects.create(
            organization=org, vehicle=v, record_type='fuel_fill',
            occurred_at=now, quantity=40, energy_basis='grid', is_full=True,
            cost=3600, currency='INR', origin='organization_provided')
        EnergyRecord.objects.create(
            organization=org, vehicle=v, record_type='fuel_fill',
            occurred_at=now, quantity=30, energy_basis='grid', is_full=True,
            origin='organization_provided')
        MaintenanceEvent.objects.create(
            organization=org, vehicle=v, event_type='repair',
            occurred_on=date.today(), origin='organization_provided')
        r = self.w.client.get('/api/costs/recorded?months=3&powertrain=ice')
        self.assertEqual(r.status_code, 200, r.content)
        cats = r.data['categories']
        self.assertEqual(cats['fuel']['total'], 3600.0)
        self.assertEqual(cats['fuel']['without_cost'], 1)
        self.assertIsNone(cats['maintenance']['total'])
        self.assertEqual(cats['maintenance']['without_cost'], 1)
        months = r.data['months']
        self.assertEqual(len(months), 3)
        self.assertIsNone(months[0]['fuel'])
        self.assertEqual(months[-1]['fuel'], 3600.0)
        self.assertIsNone(months[-1]['maintenance'])
        self.assertEqual(self.w.client.get(
            '/api/costs/recorded?months=99').status_code, 400)
        other = World()
        o = other.client.get('/api/costs/recorded').data
        self.assertIsNone(o['categories']['fuel']['total'])
