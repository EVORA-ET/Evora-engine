"""Load cited EV catalogue entries from a JSON file.

Used by the seed data migration and the `load_ev_catalogue` command. An
existing entry (same manufacturer, model, variant) is updated with the file's
figures; its verification is reset to unverified when a figure changes, so a
staff verification always refers to the values it checked.
"""

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

SEED = Path(__file__).with_name('seed_2026_10.json')

SPEC_FIELDS = (
    'vehicle_type', 'body', 'battery_capacity_kwh', 'battery_capacity_is_usable',
    'energy_consumption_kwh_per_km', 'published_range_km', 'range_basis',
    'range_cycle', 'payload_kg', 'gvw_kg', 'max_ac_charge_kw',
    'max_dc_charge_kw', 'charging_notes', 'list_price', 'price_currency',
    'price_basis', 'price_as_of', 'source_title', 'source_publisher',
    'source_url', 'source_published_on', 'notes',
)
DECIMALS = {'battery_capacity_kwh', 'energy_consumption_kwh_per_km',
            'published_range_km', 'payload_kg', 'gvw_kg', 'max_ac_charge_kw',
            'max_dc_charge_kw', 'list_price'}
DATES = {'price_as_of', 'source_published_on'}


def _clean(field, value):
    if value is None:
        return None
    if field in DECIMALS:
        return Decimal(str(value))
    if field in DATES:
        return date.fromisoformat(value)
    return value


def load(model_cls, path=SEED):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    retrieved = datetime.fromisoformat(data['retrieved_at'].replace('Z', '+00:00'))
    created = updated = 0
    for row in data['models']:
        key = {k: row[k] for k in ('manufacturer', 'model', 'variant')}
        values = {f: _clean(f, row.get(f)) for f in SPEC_FIELDS}
        for f in ('range_basis', 'range_cycle', 'charging_notes', 'price_currency',
                  'price_basis', 'body', 'notes'):
            values[f] = values[f] or ''
        obj = model_cls.objects.filter(**key).first()
        if obj is None:
            model_cls.objects.create(**key, **values, retrieved_at=retrieved)
            created += 1
            continue
        changed = [f for f, v in values.items() if getattr(obj, f) != v]
        if changed:
            for f in changed:
                setattr(obj, f, values[f])
            obj.retrieved_at = retrieved
            obj.verification_status = 'unverified'
            obj.verified_by_name = ''
            obj.verified_at = None
            obj.verification_note = ''
            obj.save()
            updated += 1
    return created, updated
