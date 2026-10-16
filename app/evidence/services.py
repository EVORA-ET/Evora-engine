"""Evidence write-validation, freshness and input resolution.

Write rules (enforced for the API, the CSV import and any future adapter):
* the unit must equal the kind's canonical unit - nothing is converted;
* the value must be inside the kind's plausible range;
* the subject (vehicle / depot / EV candidate / organization) must be one the
  kind describes, and belong to the caller's organization;
* required qualifiers must be present and from the allowed values;
* instant kinds need observed_at (not in the future), period kinds a period,
  effective kinds an effective_from date;
* measured and external values must cite a source that is not rejected;
* an odometer reading must fit between its neighbours in time.

Resolution: `ResolvedInput`s are built from observations with their source
reference, dates, evidence class and staleness, all frozen into assessment
snapshots.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from evidence import kinds as K
from evidence import provenance as P
from evidence.models import (
    EnergyRecord,
    EvidenceSource,
    Observation,
    RefreshStatus,
    ValidationStatus,
    VerificationStatus,
)

FUTURE_TOLERANCE = timedelta(minutes=10)


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def _subject_of(data):
    present = [s for s in ('vehicle', 'depot', 'ev_candidate')
               if data.get(s) is not None]
    if len(present) > 1:
        raise ValidationError(
            {'subject': 'An observation describes one subject: give at most '
             'one of vehicle_id, depot_id, ev_candidate_id.'})
    return present[0] if present else K.ORGANIZATION


def validate_qualifiers(kind, qualifiers):
    qualifiers = qualifiers or {}
    if not isinstance(qualifiers, dict):
        raise ValidationError({'qualifiers': 'Must be an object.'})
    errors = []
    for key in qualifiers:
        if key not in kind.qualifiers:
            errors.append(f'Unknown qualifier "{key}" for {kind.key}.')
    for key in kind.required_qualifiers:
        if qualifiers.get(key) in (None, ''):
            errors.append(f'Qualifier "{key}" is required for {kind.key}.')
    for key, allowed in kind.qualifiers.items():
        value = qualifiers.get(key)
        if value is not None and allowed is not None and value not in allowed:
            errors.append(f'Qualifier "{key}" must be one of: '
                          f'{", ".join(allowed)}.')
    if kind.key == 'residual_value' and 'horizon_years' in qualifiers:
        h = qualifiers['horizon_years']
        if not isinstance(h, int) or isinstance(h, bool) or not 1 <= h <= 30:
            errors.append('Qualifier "horizon_years" must be a whole number '
                          'from 1 to 30.')
    if errors:
        raise ValidationError({'qualifiers': errors})
    return qualifiers


def validate_observation(org, data):
    """Validate a dict of observation fields (model objects for FKs).

    Returns the cleaned dict ready for Observation(**data). Raises
    ValidationError with field-keyed messages.
    """
    kind = K.get_kind(data.get('kind'))
    if kind is None:
        raise ValidationError(
            {'kind': f'Unknown kind. Use one of: {", ".join(K.KINDS)}.'})

    expected_unit = kind.unit_for(org.currency)
    if data.get('unit') != expected_unit:
        raise ValidationError({'unit': (
            f'"{data.get("unit")}" is not accepted for {kind.key}; the unit '
            f'must be "{expected_unit}". EVORA stores one canonical unit per '
            'kind and does not convert.')})

    value = data.get('value')
    if value is None:
        raise ValidationError({'value': 'This field is required.'})
    if not K.in_range(kind, float(value)):
        raise ValidationError({'value': (
            f'{value} {expected_unit} is outside the plausible range for '
            f'{kind.label} ({K.range_text(kind)}).')})

    subject = _subject_of(data)
    if subject not in kind.subjects:
        raise ValidationError({'subject': (
            f'{kind.key} describes: {", ".join(kind.subjects)}; '
            f'got {subject}.')})
    for field in ('vehicle', 'depot', 'ev_candidate'):
        obj = data.get(field)
        if obj is not None and obj.organization_id != org.pk:
            raise ValidationError({f'{field}_id': 'Not found.'})

    data['qualifiers'] = validate_qualifiers(kind, data.get('qualifiers'))

    now = timezone.now()
    if kind.temporal == K.INSTANT:
        at = data.get('observed_at')
        if at is None:
            raise ValidationError(
                {'observed_at': f'{kind.key} is a reading; observed_at is '
                 'required.'})
        if at > now + FUTURE_TOLERANCE:
            raise ValidationError({'observed_at': 'Cannot be in the future.'})
    elif kind.temporal == K.PERIOD:
        ps, pe = data.get('period_start'), data.get('period_end')
        if ps is None or pe is None:
            raise ValidationError(
                {'period_start': f'{kind.key} describes a period; '
                 'period_start and period_end are required.'})
        if pe < ps:
            raise ValidationError(
                {'period_end': 'Must not be before period_start.'})
    else:
        ef = data.get('effective_from')
        if ef is None:
            raise ValidationError(
                {'effective_from': f'{kind.key} is in force from a date; '
                 'effective_from is required.'})
        et = data.get('effective_to')
        if et is not None and et < ef:
            raise ValidationError(
                {'effective_to': 'Must not be before effective_from.'})

    origin = data.get('origin')
    if origin not in P.ORIGINS:
        raise ValidationError(
            {'origin': f'Use one of: {", ".join(P.ORIGINS)}.'})
    source = data.get('source')
    if origin in P.ORIGINS_REQUIRING_SOURCE and source is None:
        raise ValidationError({'source_id': (
            f'A {origin} value must cite the source it was taken from.')})
    if source is not None:
        if source.organization_id != org.pk:
            raise ValidationError({'source_id': 'Not found.'})
        if source.verification_status == VerificationStatus.REJECTED:
            raise ValidationError(
                {'source_id': 'This source was rejected during verification.'})
    if origin == P.MODELLED and not (data.get('transformation') or '').strip():
        raise ValidationError({'transformation': (
            'A modelled value must describe how it was calculated.')})

    _check_duplicate(org, kind, data)
    if kind.key == 'odometer_km':
        _check_odometer_order(org, data)
    return data


def _check_duplicate(org, kind, data):
    """Refuse a second current observation describing the same thing.

    Same kind, same subject, same qualifiers and the same time key (instant:
    observed_at; period: start+end; effective: effective_from). A different
    value for the same key is a correction and must use /supersede, so two
    conflicting values can never both be "current".
    """
    qs = current_observations(org).filter(
        kind=kind.key, vehicle=data.get('vehicle'), depot=data.get('depot'),
        ev_candidate=data.get('ev_candidate'),
        qualifiers=data.get('qualifiers') or {})
    if kind.temporal == K.INSTANT:
        qs = qs.filter(observed_at=data['observed_at'])
    elif kind.temporal == K.PERIOD:
        qs = qs.filter(period_start=data['period_start'],
                       period_end=data['period_end'])
    else:
        qs = qs.filter(effective_from=data['effective_from'])
    sup = data.get('supersedes')
    if sup is not None:
        qs = qs.exclude(pk=sup.pk)
    existing = qs.first()
    if existing is not None:
        raise ValidationError({'non_field_errors': [
            f'An observation of {kind.key} for this subject and time already '
            f'exists ({existing.pk}, value {existing.value}). To change it, '
            'supersede it.']})


def _check_odometer_order(org, data):
    vehicle, at, value = data['vehicle'], data['observed_at'], data['value']
    current = current_observations(org).filter(
        kind='odometer_km', vehicle=vehicle)
    sup = data.get('supersedes')
    if sup is not None:
        current = current.exclude(pk=sup.pk)
    before = current.filter(observed_at__lte=at).order_by(
        '-observed_at').first()
    after = current.filter(observed_at__gte=at).order_by('observed_at').first()
    if before and float(before.value) > float(value):
        raise ValidationError({'value': (
            f'Odometer {value} km at {at:%Y-%m-%d %H:%M} is lower than the '
            f'earlier reading {before.value} km at '
            f'{before.observed_at:%Y-%m-%d %H:%M}. If that reading is wrong, '
            'supersede it.')})
    if after and float(after.value) < float(value):
        raise ValidationError({'value': (
            f'Odometer {value} km at {at:%Y-%m-%d %H:%M} is higher than the '
            f'later reading {after.value} km at '
            f'{after.observed_at:%Y-%m-%d %H:%M}.')})


def create_observation(org, user, data):
    data = validate_observation(org, dict(data))
    return Observation.objects.create(
        organization=org, created_by=user,
        validation_status=ValidationStatus.VALID, **data)


def current_observations(org):
    """Observations not superseded by a correction."""
    return Observation.objects.filter(
        organization=org, superseded_by__isnull=True)


# ---------------------------------------------------------------------------
# Freshness and resolution
# ---------------------------------------------------------------------------


def _reference_date(obs):
    if obs.observed_at:
        return obs.observed_at.date()
    if obs.period_end:
        return obs.period_end
    if obs.effective_from:
        return obs.effective_from
    return obs.created_at.date()


def source_refresh_problem(source, as_of):
    if source is None:
        return ''
    if source.last_refresh_status == RefreshStatus.FAILED:
        return (f'the last refresh of source "{source.title}" failed'
                + (f' ({source.last_refresh_error})'
                   if source.last_refresh_error else ''))
    if source.refresh_interval_days and source.last_successful_refresh_at:
        due = (source.last_successful_refresh_at.date()
               + timedelta(days=source.refresh_interval_days))
        if as_of > due:
            return (f'source "{source.title}" was due for refresh on '
                    f'{due.isoformat()}')
    return ''


def staleness(obs, as_of):
    kind = K.get_kind(obs.kind)
    if obs.effective_to and obs.effective_to < as_of:
        return True, f'expired on {obs.effective_to.isoformat()}'
    if obs.effective_from and obs.effective_from > as_of:
        return True, f'not in force until {obs.effective_from.isoformat()}'
    ref = _reference_date(obs)
    if kind and kind.max_age_days is not None:
        age = (as_of - ref).days
        if age > kind.max_age_days:
            return True, (f'{age} days old; {kind.key} is considered stale '
                          f'after {kind.max_age_days} days')
    problem = source_refresh_problem(obs.source, as_of)
    if problem:
        return True, problem
    return False, ''


def resolved_from_observation(obs, name, as_of, *, critical=True,
                              transformation='', note=''):
    stale, reason = staleness(obs, as_of)
    source = obs.source
    return P.ResolvedInput(
        name=name,
        value=float(obs.value),
        unit=obs.unit,
        origin=obs.origin,
        evidence_class=P.evidence_class(
            obs.origin, bool(source and source.is_verified), stale),
        stale=stale,
        stale_reason=reason,
        observation_id=str(obs.pk),
        source=source.as_reference() if source else None,
        observed_at=obs.observed_at.isoformat() if obs.observed_at else None,
        period=({'start': obs.period_start.isoformat(),
                 'end': obs.period_end.isoformat()}
                if obs.period_start else None),
        effective=({'from': obs.effective_from.isoformat(),
                    'to': (obs.effective_to.isoformat()
                           if obs.effective_to else None)}
                   if obs.effective_from else None),
        qualifiers=obs.qualifiers,
        transformation=transformation or obs.transformation,
        note=note or obs.note,
        critical=critical,
    )


def resolved_from_record(name, value, unit, *, origin, source, as_of,
                         transformation, critical=True, note=''):
    """For values on a domain record (e.g. an EVCandidate spec field)."""
    stale_reason = source_refresh_problem(source, as_of) if source else ''
    stale = bool(stale_reason)
    return P.ResolvedInput(
        name=name, value=None if value is None else float(value), unit=unit,
        origin=origin,
        evidence_class=(P.MISSING if value is None else P.evidence_class(
            origin, bool(source and source.is_verified), stale)),
        stale=stale, stale_reason=stale_reason,
        source=source.as_reference() if source else None,
        transformation=transformation, note=note, critical=critical)


_CLASS_RANK = {
    P.MEASURED: 0, P.VERIFIED_EXTERNAL: 0,
    P.ORGANIZATION_PROVIDED: 1, P.UNVERIFIED_EXTERNAL: 1, P.MODELLED: 1,
    P.ASSUMED: 2, P.STALE: 2, P.MISSING: 3,
}


def combine(name, value, unit, parts, transformation, *, critical=True):
    """A value derived from other resolved inputs. It inherits the weakest
    evidence class of its parts; its own origin is 'modelled'."""
    if any(not p.present for p in parts) or value is None:
        return P.ResolvedInput.missing(
            name, unit, critical=critical,
            note='Cannot derive: ' + ', '.join(
                p.name for p in parts if not p.present) + ' missing.')
    weakest = max(parts, key=lambda p: _CLASS_RANK.get(p.evidence_class, 3))
    stale = any(p.stale for p in parts)
    return P.ResolvedInput(
        name=name, value=value, unit=unit, origin=P.MODELLED,
        evidence_class=weakest.evidence_class,
        stale=stale,
        stale_reason='; '.join(p.stale_reason for p in parts if p.stale),
        transformation=transformation,
        note='Derived from: ' + ', '.join(
            f'{p.name} ({p.evidence_class})' for p in parts),
        critical=critical,
    )


def latest_observation(org, kind, as_of, *, vehicle=None, depot=None,
                       ev_candidate=None, organization_level=False,
                       qualifiers=None):
    """Most relevant current, valid observation for a subject.

    Prefers one in force at `as_of`; falls back to the most recent one (which
    will then resolve as stale). Rejected sources are never used.
    """
    qs = current_observations(org).filter(
        kind=kind, validation_status=ValidationStatus.VALID).exclude(
        source__verification_status=VerificationStatus.REJECTED)
    if vehicle is not None:
        qs = qs.filter(vehicle=vehicle)
    elif depot is not None:
        qs = qs.filter(depot=depot)
    elif ev_candidate is not None:
        qs = qs.filter(ev_candidate=ev_candidate)
    elif organization_level:
        qs = qs.filter(vehicle__isnull=True, depot__isnull=True,
                       ev_candidate__isnull=True)
    for key, value in (qualifiers or {}).items():
        qs = qs.filter(**{f'qualifiers__{key}': value})
    qs = qs.select_related('source')
    in_force = qs.filter(
        Q(effective_from__isnull=True) | Q(effective_from__lte=as_of)).filter(
        Q(effective_to__isnull=True) | Q(effective_to__gte=as_of))
    order = ('-effective_from', '-observed_at', '-period_end', '-created_at')
    return in_force.order_by(*order).first() or qs.order_by(*order).first()


def resolve_explicit(org, name, spec, *, kind_key, unit, as_of,
                     critical=True, allowed_subjects=None):
    """A caller-supplied input: {"observation_id": ...} or an inline
    {"value", "unit", "origin": organization_provided|assumed, "note"}.

    Inline values cannot claim to be measured or verified: those must be
    stored as observations with a source first.
    """
    if not isinstance(spec, dict):
        raise ValidationError({name: 'Must be an object.'})
    if spec.get('observation_id'):
        obs = (current_observations(org).select_related('source')
               .filter(pk=spec['observation_id']).first())
        if obs is None:
            superseded = Observation.objects.filter(
                organization=org, pk=spec['observation_id']).first()
            if superseded is not None:
                raise ValidationError({name: (
                    'That observation has been superseded by a correction; '
                    'use the current one.')})
            raise ValidationError({name: 'Observation not found.'})
        if obs.source and obs.source.verification_status == \
                VerificationStatus.REJECTED:
            raise ValidationError({name: (
                'That observation cites a source that was rejected during '
                'verification; it cannot inform an assessment.')})
        if kind_key and obs.kind != kind_key:
            raise ValidationError(
                {name: f'Expected a {kind_key} observation, got {obs.kind}.'})
        if allowed_subjects is not None and obs.subject not in allowed_subjects:
            raise ValidationError({name: (
                f'Observation describes a {obs.subject}; expected '
                f'{" or ".join(allowed_subjects)}.')})
        return resolved_from_observation(obs, name, as_of, critical=critical)

    if 'value' not in spec:
        raise ValidationError(
            {name: 'Give either observation_id or value/unit/origin.'})
    try:
        value = float(spec['value'])
    except (TypeError, ValueError):
        raise ValidationError({name: 'value must be a number.'})
    if spec.get('unit') != unit:
        raise ValidationError({name: (
            f'unit must be "{unit}" (got "{spec.get("unit")}"); EVORA does '
            'not convert units.')})
    origin = spec.get('origin')
    if origin not in P.INLINE_ORIGINS:
        raise ValidationError({name: (
            'Inline values must have origin organization_provided or assumed. '
            'Record measured or external values as observations with a '
            'source.')})
    kind = K.get_kind(kind_key) if kind_key else None
    if kind and not K.in_range(kind, value):
        raise ValidationError({name: (
            f'{value} {unit} is outside the plausible range '
            f'({K.range_text(kind)}).')})
    return P.ResolvedInput(
        name=name, value=value, unit=unit, origin=origin,
        evidence_class=origin, note=str(spec.get('note') or ''),
        transformation='Entered with this assessment request.',
        critical=critical)


def first_available(name, unit, *attempts, critical=True, missing_note=''):
    """Return the first attempt that yields a present ResolvedInput."""
    for attempt in attempts:
        resolved = attempt()
        if resolved is not None and resolved.present:
            resolved.name = name
            resolved.critical = critical
            return resolved
    return P.ResolvedInput.missing(name, unit, note=missing_note,
                                   critical=critical)


# ---------------------------------------------------------------------------
# Usage profile (shared by EV Migration and Health & Maintenance)
# ---------------------------------------------------------------------------


def vehicle_usage(org, vehicle, window_start, window_end, tz):
    from evidence.usage import EnergyEntry, Reading, fill_to_fill, \
        odometer_profile
    from job_instances.models import JobInstance, JobInstanceStatus

    readings = [
        Reading(at=o.observed_at, km=float(o.value), id=str(o.pk))
        for o in current_observations(org).filter(
            kind='odometer_km', vehicle=vehicle,
            validation_status=ValidationStatus.VALID,
        ).exclude(source__verification_status=VerificationStatus.REJECTED)
    ]
    odometer = odometer_profile(readings, window_start, window_end, tz)

    records = EnergyRecord.objects.filter(
        vehicle=vehicle, organization=org, voided_at__isnull=True,
        occurred_at__gte=window_start, occurred_at__lte=window_end)

    def entries(record_type):
        return [EnergyEntry(
            at=r.occurred_at, quantity=float(r.quantity), is_full=r.is_full,
            odometer_km=(float(r.odometer_km)
                         if r.odometer_km is not None else None),
            basis=r.energy_basis, id=str(r.pk))
            for r in records if r.record_type == record_type]

    fuel = fill_to_fill(entries('fuel_fill'), kind='fuel')
    charging = fill_to_fill(entries('charging_session'), kind='charging')

    jobs = JobInstance.objects.filter(
        organization=org, vehicle=vehicle,
        status=JobInstanceStatus.COMPLETED,
        actual_start_at__gte=window_start, actual_start_at__lte=window_end,
    ).select_related('job_template')
    per_day = {}
    without_estimate = 0
    for job in jobs:
        est = job.job_template.estimated_distance_km
        if est is None:
            without_estimate += 1
            continue
        day = job.actual_start_at.astimezone(tz).date()
        per_day[day] = per_day.get(day, 0.0) + float(est)
    planned = {
        'basis': 'planned',
        'definition': ('Route estimated_distance_km of COMPLETED jobs, grouped '
                       'by the local day of actual_start_at. These are route '
                       'plans, not measured distance.'),
        'completed_jobs': len(jobs),
        'completed_jobs_without_distance_estimate': without_estimate,
        'days_with_planned_distance': len(per_day),
        'total_planned_km': round(sum(per_day.values()), 2) if per_day else None,
        'max_planned_day_km': round(max(per_day.values()), 2) if per_day else None,
    }

    legacy = {
        'basis': 'organization_provided',
        'definition': ('Single values typed on the vehicle record; no date or '
                       'source is stored for them. Used only as a labelled '
                       'last resort.'),
        'odometer_km': _f(vehicle.odometer_km),
        'avg_daily_km': _f(vehicle.avg_daily_km),
        'avg_monthly_km': _f(vehicle.avg_monthly_km),
        'mileage_kmpl': _f(vehicle.mileage_kmpl),
        'fuel_cost_per_month': _f(vehicle.fuel_cost_per_month),
        'maintenance_cost_per_month': _f(vehicle.maintenance_cost_per_month),
    }
    return {
        'vehicle_id': str(vehicle.pk),
        'registration_number': vehicle.registration_number,
        'measured': {
            'odometer': odometer,
            'fuel_consumption': fuel,
            'energy_consumption': charging,
        },
        'planned': planned,
        'vehicle_record': legacy,
    }


def _f(value):
    return None if value is None else float(value)


def window_from_request(request, org, default_days=365, max_days=1095):
    """Usage window ending now: `?window_days=` (default 365)."""
    raw = request.query_params.get('window_days')
    days = default_days
    if raw:
        try:
            days = int(raw)
        except ValueError:
            raise ValidationError({'window_days': 'Must be a whole number.'})
        if not 1 <= days <= max_days:
            raise ValidationError(
                {'window_days': f'Must be between 1 and {max_days}.'})
    end = timezone.now()
    return end - timedelta(days=days), end, days


def today_for(org):
    """(local date, tz) in the organization's configured timezone."""
    try:
        tz = ZoneInfo(org.timezone or 'UTC')
    except (ZoneInfoNotFoundError, ValueError):
        tz = ZoneInfo('UTC')
    return timezone.now().astimezone(tz).date(), tz


def as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def source_for(org, source_id):
    if source_id is None:
        return None
    return EvidenceSource.objects.filter(organization=org, pk=source_id).first()
