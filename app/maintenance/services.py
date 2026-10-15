"""Health & Maintenance: gather records -> snapshot -> rules -> (persist)."""

from datetime import date, timedelta

from rest_framework.exceptions import ValidationError

from evidence import assessments, provenance as P
from evidence import services as S
from evidence.models import AssessmentKind
from maintenance import rules
from maintenance.models import MaintenanceEvent, ServiceRequirement
from vehicles.models import FuelType


def _reading(obs, as_of):
    if obs is None:
        return None
    stale, reason = S.staleness(obs, as_of)
    return {
        'value': float(obs.value),
        'at': obs.observed_at.isoformat() if obs.observed_at else
        (obs.effective_from.isoformat() if obs.effective_from else None),
        'stale': stale, 'stale_reason': reason,
        'method': obs.qualifiers.get('method', ''),
        'observation_id': str(obs.pk),
        'source': obs.source.as_reference() if obs.source else None,
        'origin': obs.origin,
        'evidence_class': P.evidence_class(
            obs.origin, bool(obs.source and obs.source.is_verified), stale),
    }


def _latest_instant(org, vehicle, kind):
    return (S.current_observations(org)
            .filter(kind=kind, vehicle=vehicle, validation_status='valid')
            .select_related('source').order_by('-observed_at').first())


def build_snapshot(org, vehicle, as_of, window_days):
    reqs = ServiceRequirement.objects.filter(
        organization=org, vehicle=vehicle, retired_at__isnull=True
    ).select_related('source')
    events = MaintenanceEvent.objects.filter(
        organization=org, vehicle=vehicle, voided_at__isnull=True)
    threshold = S.latest_observation(
        org, 'battery_warranty_soh_threshold_pct', as_of, vehicle=vehicle)
    odometer = _latest_instant(org, vehicle, 'odometer_km')
    return {
        'inputs': _evidence_inputs(reqs, events, odometer, as_of),
        'context': {
            'vehicle': {
                'id': str(vehicle.pk),
                'registration_number': vehicle.registration_number,
                'fuel_type': vehicle.fuel_type,
                'purchase_date': vehicle.purchase_date.isoformat(),
                'battery_capacity_kwh': (float(vehicle.battery_capacity_kwh)
                                         if vehicle.battery_capacity_kwh
                                         is not None else None),
                'status': vehicle.status,
            },
            'requirements': [{
                'id': str(r.pk), 'name': r.name, 'component': r.component,
                'interval_km': _f(r.interval_km),
                'interval_days': r.interval_days,
                'warning_km': _f(r.warning_km),
                'warning_days': r.warning_days,
                'source': r.source.as_reference(),
            } for r in reqs],
            'events': [{
                'id': str(e.pk), 'event_type': e.event_type,
                'occurred_on': e.occurred_on.isoformat(),
                'odometer_km': _f(e.odometer_km),
                'requirement_id': (str(e.requirement_id)
                                   if e.requirement_id else None),
                'resolves_id': str(e.resolves_id) if e.resolves_id else None,
                'fault_code': e.fault_code,
                'fault_severity': e.fault_severity,
                'component': e.component, 'description': e.description,
                'cost': _f(e.cost), 'currency': e.currency,
                'downtime_hours': _f(e.downtime_hours),
                'origin': e.origin,
                'source_id': str(e.source_id) if e.source_id else None,
            } for e in events],
            'odometer': _reading(_latest_instant(org, vehicle, 'odometer_km'),
                                 as_of),
            'battery_soh': _reading(
                _latest_instant(org, vehicle, 'battery_soh_pct'), as_of),
            'battery_soc': _reading(
                _latest_instant(org, vehicle, 'battery_soc_pct'), as_of),
            'battery_capacity': _reading(
                _latest_instant(org, vehicle, 'battery_capacity_kwh'), as_of),
            'battery_warranty_threshold': _reading(threshold, as_of),
        },
    }


def _f(v):
    return None if v is None else float(v)


def _evidence_inputs(reqs, events, odometer, as_of):
    """What the schedule checks rest on, expressed as ResolvedInputs so the
    shared coverage rules apply:

    * service requirements (critical) - their evidence class is the weakest
      of their cited sources;
    * the current odometer (critical only when a km interval exists);
    * the maintenance history (supporting; an empty history is reported,
      never filled in).
    """
    reqs = list(reqs)
    if reqs:
        classes = {P.evidence_class(P.EXTERNAL, r.source.is_verified, False)
                   for r in reqs}
        req_input = P.ResolvedInput(
            name='service_requirements', value=float(len(reqs)),
            unit='count', origin=P.EXTERNAL,
            evidence_class=(P.UNVERIFIED_EXTERNAL
                            if P.UNVERIFIED_EXTERNAL in classes
                            else P.VERIFIED_EXTERNAL),
            transformation='Intervals from the cited documents.')
    else:
        req_input = P.ResolvedInput.missing(
            'service_requirements', 'count',
            note='No documented service intervals recorded.')
    needs_km = any(r.interval_km for r in reqs)
    if odometer is not None:
        odo = S.resolved_from_observation(
            odometer, 'current_odometer_km', as_of, critical=needs_km)
    else:
        odo = P.ResolvedInput.missing('current_odometer_km', 'km',
                                      critical=needs_km)
    events = list(events)
    history = P.ResolvedInput(
        name='maintenance_events', value=float(len(events)), unit='count',
        origin=P.ORGANIZATION_PROVIDED,
        evidence_class=P.ORGANIZATION_PROVIDED if events else P.MISSING,
        critical=False,
        note='' if events else 'No maintenance history recorded.')
    if not events:
        history.value = None
    return {i.name: i.to_dict() for i in (req_input, odo, history)}


def _as_reading(d):
    if d is None:
        return None
    return rules.Reading(value=d['value'], at=d['at'], stale=d['stale'],
                         stale_reason=d['stale_reason'], method=d['method'],
                         observation_id=d['observation_id'], source=d['source'])


def compute(snapshot, params):
    ctx = snapshot['context']
    today = date.fromisoformat(params['as_of'])
    window_start = today - timedelta(days=params['window_days'])
    vehicle = ctx['vehicle']
    reqs = [rules.Requirement(
        id=r['id'], name=r['name'], component=r['component'],
        interval_km=r['interval_km'], interval_days=r['interval_days'],
        warning_km=r['warning_km'], warning_days=r['warning_days'],
        source=r['source']) for r in ctx['requirements']]
    events = [rules.Event(
        id=e['id'], event_type=e['event_type'],
        occurred_on=date.fromisoformat(e['occurred_on']),
        odometer_km=e['odometer_km'], requirement_id=e['requirement_id'],
        resolves_id=e['resolves_id'], fault_code=e['fault_code'],
        fault_severity=e['fault_severity'], component=e['component'],
        description=e['description'], cost=e['cost'], currency=e['currency'],
        downtime_hours=e['downtime_hours']) for e in ctx['events']]
    battery = rules.battery_check(
        is_ev=vehicle['fuel_type'] == FuelType.EV,
        nominal_kwh=vehicle['battery_capacity_kwh'],
        soh=_as_reading(ctx['battery_soh']),
        threshold=_as_reading(ctx['battery_warranty_threshold']),
        capacity=_as_reading(ctx['battery_capacity']),
        soc=_as_reading(ctx['battery_soc']))
    result = rules.evaluate(
        today=today,
        purchase_date=date.fromisoformat(vehicle['purchase_date']),
        requirements=reqs, events=events,
        odometer=_as_reading(ctx['odometer']), battery=battery,
        window_start=window_start, window_end=today)
    if vehicle['status'] == 'under_maintenance':
        result['messages'].append(
            'The vehicle record is marked "under maintenance". That status is '
            'set manually and is not, by itself, evidence of a fault.')
    return result


assessments.register(rules.METHOD_VERSION, compute)


def parse_window(raw):
    if raw in (None, ''):
        return 365
    try:
        days = int(raw)
    except (TypeError, ValueError):
        raise ValidationError({'window_days': 'Must be a whole number.'})
    if not 1 <= days <= 3650:
        raise ValidationError({'window_days': 'Must be between 1 and 3650.'})
    return days


def status_for(org, vehicle, window_days):
    """Live (not persisted) evaluation."""
    as_of, _ = S.today_for(org)
    params = {'as_of': as_of.isoformat(), 'window_days': window_days}
    snapshot = build_snapshot(org, vehicle, as_of, window_days)
    return compute(snapshot, params), snapshot, params


def persist_status(org, user, vehicle, window_days):
    as_of, _ = S.today_for(org)
    params = {'as_of': as_of.isoformat(), 'window_days': window_days}
    snapshot = build_snapshot(org, vehicle, as_of, window_days)
    return assessments.persist(
        org=org, user=user, kind=AssessmentKind.MAINTENANCE_STATUS,
        method_version=rules.METHOD_VERSION, snapshot=snapshot,
        parameters=params, vehicle=vehicle,
        subject_snapshot={'vehicle': snapshot['context']['vehicle']})
