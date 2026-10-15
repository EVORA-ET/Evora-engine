"""Deterministic maintenance checks (plain Python, no Django).

Method ``maintenance-rules/1.0.0``. These are schedule and record checks,
not predictions of failure.

Service requirement (each interval taken from a cited document):
  time   due_date = last satisfying event date + interval_days
         (no recorded event: vehicle purchase date + interval_days, flagged)
  km     due_km   = odometer at last satisfying event + interval_km
         (no event with an odometer: undetermined - the odometer at purchase
         is not recorded, so no km baseline is invented)
  status per dimension: overdue (past due) / due_soon (within the
         requirement's own warning window) / ok. A requirement's status is the
         worst determined dimension; 'undetermined' when none can be checked.

Faults: a fault_report with no later event that `resolves` it is open and
  requires review. Severity is whatever was recorded.

Battery (EVs only; SoC is never used as a health input):
  - latest state-of-health observation vs the warranty threshold from the
    warranty document, when both exist;
  - capacity retention = measured usable capacity / nominal capacity on the
    vehicle record, labelled 'measured' for capacity tests and 'estimated' for
    BMS estimates;
  - otherwise: "cannot be assessed from the available evidence".
"""

from dataclasses import dataclass, field
from datetime import timedelta

METHOD_VERSION = 'maintenance-rules/1.0.0'

OVERDUE, DUE_SOON, OK, UNDETERMINED = 'overdue', 'due_soon', 'ok', \
    'undetermined'
_RANK = {OVERDUE: 3, DUE_SOON: 2, OK: 1}
SERVICE_EVENT_TYPES = ('scheduled_service', 'inspection',
                       'component_replacement')


@dataclass
class Requirement:
    id: str
    name: str
    interval_km: float | None
    interval_days: int | None
    warning_km: float | None
    warning_days: int | None
    component: str = ''
    source: dict = field(default_factory=dict)


@dataclass
class Event:
    id: str
    event_type: str
    occurred_on: object  # date
    odometer_km: float | None = None
    requirement_id: str | None = None
    resolves_id: str | None = None
    fault_code: str = ''
    fault_severity: str = ''
    component: str = ''
    description: str = ''
    cost: float | None = None
    currency: str = ''
    downtime_hours: float | None = None


@dataclass
class Reading:
    value: float
    at: str
    stale: bool = False
    stale_reason: str = ''
    method: str = ''
    observation_id: str = ''
    source: dict | None = None


def _status(remaining, warning):
    if remaining < 0:
        return OVERDUE
    if warning is not None and remaining <= warning:
        return DUE_SOON
    return OK


def check_requirement(req, events, today, purchase_date, odometer):
    done = sorted(
        (e for e in events if e.requirement_id == req.id
         and e.event_type in SERVICE_EVENT_TYPES),
        key=lambda e: e.occurred_on)
    last = done[-1] if done else None
    dims = {}
    notes = []

    if req.interval_days:
        if last:
            base, basis = last.occurred_on, f'last recorded event {last.id}'
        else:
            base, basis = purchase_date, 'vehicle purchase date (no event recorded)'
            notes.append('No completed service recorded for this requirement; '
                         'the time interval is counted from the purchase date.')
        due = base + timedelta(days=req.interval_days)
        remaining = (due - today).days
        dims['time'] = {
            'status': _status(remaining, req.warning_days),
            'baseline_date': base.isoformat(), 'baseline_basis': basis,
            'due_date': due.isoformat(), 'days_remaining': remaining,
            'warning_days': req.warning_days,
        }

    if req.interval_km:
        last_km = last.odometer_km if last else None
        if last_km is None:
            dims['distance'] = {
                'status': UNDETERMINED,
                'reason': ('No completed service with an odometer reading is '
                           'recorded, so the distance since service is unknown.'),
            }
        elif odometer is None:
            dims['distance'] = {
                'status': UNDETERMINED,
                'reason': 'No odometer reading is recorded for this vehicle.',
            }
        else:
            due_km = last_km + req.interval_km
            remaining = due_km - odometer.value
            dims['distance'] = {
                'status': _status(remaining, req.warning_km),
                'baseline_odometer_km': last_km,
                'due_odometer_km': due_km,
                'current_odometer_km': odometer.value,
                'current_odometer_at': odometer.at,
                'km_remaining': round(remaining, 1),
                'warning_km': req.warning_km,
            }
            if odometer.stale:
                notes.append('The latest odometer reading is stale '
                             f'({odometer.stale_reason}); distance status may '
                             'be out of date.')

    determined = [d['status'] for d in dims.values()
                  if d['status'] != UNDETERMINED]
    status = max(determined, key=_RANK.get) if determined else UNDETERMINED
    if req.warning_days is None and req.interval_days:
        notes.append('No warning window set for the time interval; only '
                     'overdue is reported.')
    return {
        'requirement_id': req.id,
        'name': req.name,
        'component': req.component,
        'interval_km': req.interval_km,
        'interval_days': req.interval_days,
        'source': req.source,
        'status': status,
        'dimensions': dims,
        'last_event_id': last.id if last else None,
        'notes': notes,
        'kind': 'schedule_check',
    }


def open_faults(events, today):
    resolved = {e.resolves_id for e in events if e.resolves_id}
    out = []
    for e in events:
        if e.event_type == 'fault_report' and e.id not in resolved:
            out.append({
                'event_id': e.id,
                'fault_code': e.fault_code,
                'severity': e.fault_severity or 'unknown',
                'component': e.component,
                'reported_on': e.occurred_on.isoformat(),
                'days_open': (today - e.occurred_on).days,
                'description': e.description,
                'status': 'review_required',
            })
    return sorted(out, key=lambda f: f['reported_on'])


def battery_check(*, is_ev, nominal_kwh, soh, threshold, capacity, soc):
    if not is_ev:
        return {'applicable': False}
    out = {
        'applicable': True,
        'state_of_health': None,
        'warranty_threshold': None,
        'warranty_check': None,
        'capacity_retention': None,
        'state_of_charge': None,
        'messages': [],
    }
    if soc is not None:
        out['state_of_charge'] = {
            'pct': soc.value, 'at': soc.at,
            'note': 'State of charge shows how full the battery was; it is not '
                    'a measure of battery health.'}
    if soh is None:
        out['messages'].append(
            'Battery health cannot be assessed: no state-of-health measurement '
            'is recorded.')
    else:
        out['state_of_health'] = {
            'pct': soh.value, 'at': soh.at, 'method': soh.method,
            'stale': soh.stale, 'stale_reason': soh.stale_reason,
            'observation_id': soh.observation_id, 'source': soh.source}
    if threshold is not None:
        out['warranty_threshold'] = {
            'pct': threshold.value, 'source': threshold.source,
            'observation_id': threshold.observation_id}
    if soh is not None and threshold is not None:
        below = soh.value < threshold.value
        out['warranty_check'] = {
            'status': 'below_threshold' if below else 'at_or_above_threshold',
            'explanation': (f'Latest SoH {soh.value:g}% ({soh.method}, {soh.at}) '
                            f'vs warranty threshold {threshold.value:g}%.'),
        }
    elif soh is not None:
        out['messages'].append('No warranty SoH threshold recorded, so the '
                               'SoH value cannot be checked against it.')
    if capacity is not None:
        if nominal_kwh:
            out['capacity_retention'] = {
                'measured_capacity_kwh': capacity.value,
                'nominal_capacity_kwh': nominal_kwh,
                'retention_pct': round(capacity.value / nominal_kwh * 100, 1),
                'basis': ('measured' if capacity.method == 'capacity_test'
                          else 'estimated'),
                'method': capacity.method, 'at': capacity.at,
                'stale': capacity.stale,
            }
        else:
            out['messages'].append(
                'A capacity reading exists but the vehicle has no nominal '
                'battery capacity recorded to compare it against.')
    return out


def records_summary(events, window_start, window_end):
    inside = [e for e in events if window_start <= e.occurred_on <= window_end]
    by_type = {}
    for e in inside:
        by_type[e.event_type] = by_type.get(e.event_type, 0) + 1
    costs = {}
    missing_cost = 0
    for e in inside:
        if e.event_type == 'fault_report':
            continue
        if e.cost is None:
            missing_cost += 1
        else:
            costs[e.currency or 'unspecified'] = round(
                costs.get(e.currency or 'unspecified', 0) + e.cost, 2)
    downtime = [e.downtime_hours for e in inside
                if e.downtime_hours is not None]
    return {
        'window': {'start': window_start.isoformat(),
                   'end': window_end.isoformat()},
        'event_count': len(inside),
        'events_by_type': by_type,
        'recorded_cost_by_currency': costs,
        'events_without_cost': missing_cost,
        'recorded_downtime_hours': round(sum(downtime), 2) if downtime else None,
        'events_with_downtime': len(downtime),
        'repair_count': by_type.get('repair', 0),
        'note': ('Totals cover recorded events only. Events without a cost or '
                 'downtime value are counted, not assumed to be zero.'),
    }


def evaluate(*, today, purchase_date, requirements, events, odometer,
             battery, window_start, window_end):
    service = [check_requirement(r, events, today, purchase_date, odometer)
               for r in requirements]
    faults = open_faults(events, today)
    messages = []
    if not requirements:
        messages.append('No service requirements are recorded for this '
                        'vehicle, so no service can be reported as due.')
    if not events:
        messages.append('No maintenance history is recorded for this vehicle.')
    return {
        'method_version': METHOD_VERSION,
        'as_of': today.isoformat(),
        'service_requirements': service,
        'counts': {
            OVERDUE: sum(1 for s in service if s['status'] == OVERDUE),
            DUE_SOON: sum(1 for s in service if s['status'] == DUE_SOON),
            OK: sum(1 for s in service if s['status'] == OK),
            UNDETERMINED: sum(1 for s in service
                              if s['status'] == UNDETERMINED),
            'open_faults': len(faults),
        },
        'open_faults': faults,
        'battery': battery,
        'records': records_summary(events, window_start, window_end),
        'predictive_assessment': {
            'available': False,
            'reason': ('No failure-prediction or remaining-useful-life model '
                       'has been trained or validated. Results above are '
                       'schedule and record checks only.'),
        },
        'messages': messages,
    }
