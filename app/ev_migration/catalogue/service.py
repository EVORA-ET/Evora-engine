"""Turn a catalogue entry into the organization's own EVCandidate.

Customers never create candidates for catalogue models: EVORA copies the
entry (figures, cited source, verification state and published price) into
organization-scoped records the existing assessments already understand.
The copy is immutable; when the catalogue entry changes, the next request
creates a fresh copy and earlier assessments keep the one they used.
"""

from django.db import transaction
from rest_framework.exceptions import ValidationError

from evidence import provenance as P
from evidence.models import EvidenceSource, SourceVerification
from evidence.services import create_observation
from ev_migration.models import (
    CatalogueVerification, ConsumptionSourceType, EVCandidate, EVModel)

SPEC_LABEL = {
    'battery_capacity_kwh': 'battery capacity',
    'energy_consumption_kwh_per_km': 'energy use (kWh/km) or range',
    'payload_kg': 'payload',
}


def _source(org, m):
    verified = m.verification_status == CatalogueVerification.VERIFIED
    src = EvidenceSource.objects.create(
        organization=org, title=m.source_title,
        source_type='manufacturer_document', publisher=m.source_publisher,
        identifier=f'evora-catalogue:{m.pk}', url=m.source_url,
        retrieval_method='document', retrieved_at=m.retrieved_at,
        published_on=m.source_published_on,
        methodology='Copied from the EVORA EV catalogue; figures as '
                    'published in the cited document.',
        notes=m.notes,
        verification_status='verified' if verified else 'unverified',
        verified_at=m.verified_at if verified else None,
        verification_note=(f'Checked by EVORA staff ({m.verified_by_name}) '
                           f'against the cited document. '
                           f'{m.verification_note}'.strip()
                           if verified else ''))
    if verified:
        SourceVerification.objects.create(
            source=src, status='verified', note=src.verification_note,
            decided_by=None)
    return src


def candidate_for(org, user, m: EVModel) -> EVCandidate:
    if not m.active or m.verification_status == CatalogueVerification.REJECTED:
        raise ValidationError({'model': 'This catalogue model is not available.'})
    missing = m.missing_specs()
    if missing:
        raise ValidationError({'model': (
            'The manufacturer document does not state '
            + ', '.join(SPEC_LABEL[x] for x in missing)
            + ', so this model cannot be assessed yet.')})

    existing = (EVCandidate.objects
                .filter(organization=org, catalogue_model=m,
                        created_at__gte=m.updated_at)
                .order_by('-created_at').first())
    if existing:
        return existing

    consumption, cst = m.consumption()
    published = cst == ConsumptionSourceType.MANUFACTURER_RATED
    origin = P.EXTERNAL if published and m.battery_capacity_is_usable \
        else P.MODELLED
    if published:
        conditions = 'Manufacturer-published energy use.'
    else:
        conditions = (
            f'Derived: {m.battery_capacity_kwh} kWh battery / '
            f'{m.published_range_km} km published range '
            f'({m.get_range_basis_display() or "basis not stated"}'
            f'{", " + m.range_cycle if m.range_cycle else ""}). '
            'Real-world use under load is usually higher.')
    if not m.battery_capacity_is_usable:
        conditions += (' Usable battery capacity is not published; the '
                       'stated capacity is used.')

    with transaction.atomic():
        src = _source(org, m)
        c = EVCandidate.objects.create(
            organization=org, manufacturer=m.manufacturer, model=m.model,
            variant=m.variant, vehicle_type=m.vehicle_type,
            usable_battery_kwh=m.battery_capacity_kwh,
            energy_consumption_kwh_per_km=consumption,
            consumption_basis='vehicle', consumption_source_type=cst,
            consumption_conditions=conditions, payload_kg=m.payload_kg,
            gvw_kg=m.gvw_kg, max_ac_charge_kw=m.max_ac_charge_kw,
            max_dc_charge_kw=m.max_dc_charge_kw,
            rated_range_km=m.published_range_km,
            rated_range_cycle=m.range_cycle or m.get_range_basis_display(),
            origin=origin, source=src, catalogue_model=m, created_by=user)
        if (m.list_price is not None and m.price_as_of
                and m.price_currency == org.currency):
            create_observation(org, user, {
                'kind': 'vehicle_purchase_price', 'value': m.list_price,
                'unit': org.currency, 'origin': P.EXTERNAL, 'ev_candidate': c,
                'source': src, 'effective_from': m.price_as_of,
                'qualifiers': {'purpose': 'published_list_price'},
                'note': f'Published list price ({m.price_basis}).',
                'transformation': 'Copied from the EVORA EV catalogue.'})
    return c
