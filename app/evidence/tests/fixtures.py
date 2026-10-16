"""Test fixtures for the evidence foundation. These values exist only inside
the test database; nothing here is shipped as data."""

from datetime import date, timedelta

from django.utils import timezone

from common.testing import World, make_vehicle
from evidence.models import EvidenceSource
from ev_migration.models import EVCandidate


def make_source(org, title='Test source', verified=False, **kw):
    s = EvidenceSource.objects.create(
        organization=org, title=title, source_type=kw.pop(
            'source_type', 'internal_record'),
        retrieval_method=kw.pop('retrieval_method', 'document'),
        retrieved_at=timezone.now() - timedelta(days=1), **kw)
    if verified:
        s.verification_status = 'verified'
        s.verified_at = timezone.now()
        s.save(update_fields=['verification_status', 'verified_at'])
    return s


class EvidenceWorld(World):
    """World + depot assignment, a verified spec source and an EV candidate."""

    def __init__(self):
        super().__init__()
        self.org.timezone = 'Asia/Kolkata'
        self.org.save(update_fields=['timezone'])
        self.vehicle.depot = self.depot_a
        self.vehicle.save(update_fields=['depot'])
        self.spec_source = make_source(self.org, 'Candidate brochure',
                                       verified=True,
                                       source_type='manufacturer_document')
        self.record_source = make_source(self.org, 'Fleet records',
                                         verified=True,
                                         source_type='internal_record')
        self.candidate = EVCandidate.objects.create(
            organization=self.org, manufacturer='Volt', model='Hauler',
            vehicle_type='truck', usable_battery_kwh='200',
            energy_consumption_kwh_per_km='0.9', consumption_basis='vehicle',
            consumption_source_type='measured_trial', payload_kg='1200',
            max_ac_charge_kw='22', max_dc_charge_kw='60',
            rated_range_km='400', origin='measured',
            source=self.spec_source)

    def post(self, url, data, **kw):
        return self.client.post(url, data, format='json', **kw)

    def observe(self, kind, value, unit, origin='measured', source=None,
                expect=201, **kw):
        body = {'kind': kind, 'value': value, 'unit': unit, 'origin': origin}
        if origin in ('measured', 'external'):
            body['source_id'] = str((source or self.record_source).pk)
        elif source is not None:
            body['source_id'] = str(source.pk)
        body.update(kw)
        r = self.post('/api/evidence/observations', body)
        assert r.status_code == expect, r.content
        return r.data

    def daily_odometer(self, days, km_per_day, start_km=10000.0):
        """End-of-day readings for the last `days` days (ending yesterday)."""
        now = timezone.now().replace(hour=12, minute=0, second=0,
                                     microsecond=0)
        km = start_km
        for i in range(days, 0, -1):
            self.observe('odometer_km', km, 'km',
                         observed_at=(now - timedelta(days=i)).isoformat(),
                         vehicle_id=str(self.vehicle.pk))
            km += km_per_day

    def org_value(self, kind, value, unit, **kw):
        kw.setdefault('effective_from', str(date.today() - timedelta(days=5)))
        return self.observe(kind, value, unit, **kw)


__all__ = ['EvidenceWorld', 'make_source', 'make_vehicle']
