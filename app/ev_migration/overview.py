"""Read-only summaries behind the EV Migration overview.

Everything here counts persisted records; nothing is estimated. Fleet
composition comes from `Vehicle.fuel_type`, assessment states from stored
readiness assessments, and costs from recorded fuel/charging records and
maintenance events (missing costs are counted, never treated as zero).
"""

from collections import defaultdict
from datetime import date

from django.db.models import Q

from evidence import services as S
from evidence.models import Assessment, EnergyRecord
from ev_migration.calc.readiness import (
    INSUFFICIENT_EVIDENCE, MEETS_REQUIREMENTS, NOT_SUITABLE)
from ev_migration.models import (
    DecisionType, MigrationDecision, MigrationPlan, MigrationPlanItem,
    PlanStatus)
from maintenance.models import MaintenanceEvent
from vehicles.models import FuelType, Vehicle

ICE_FUELS = (FuelType.DIESEL, FuelType.PETROL, FuelType.CNG, FuelType.LPG)
OUT_OF_SERVICE = ('retired', 'sold')
APPROVAL_LEVELS = ('high', 'moderate')
METHOD = 'ev-overview/1.0.0'


def in_service(org):
    return Vehicle.objects.filter(organization=org).exclude(
        status__in=OUT_OF_SERVICE)


def _pair_key(a):
    c = a.ev_candidate
    return (a.vehicle_id, str(c.catalogue_model_id or c.pk) if c else None)


def vehicle_readiness_states(org, vehicle_ids):
    """Latest readiness per (vehicle, EV model), folded into one state each.

    meets_requirements: at least one model's latest assessment meets every
    check; needs_evidence: none meets and at least one could not be decided;
    not_suitable: every assessed model fails a check; otherwise not assessed.
    """
    latest = {}
    for a in (Assessment.objects
              .filter(organization=org, kind='ev_readiness',
                      vehicle_id__in=vehicle_ids)
              .select_related('ev_candidate').order_by('created_at')):
        latest[_pair_key(a)] = a
    per_vehicle = defaultdict(list)
    for (vid, _), a in latest.items():
        per_vehicle[vid].append(a)
    states = {}
    for vid in vehicle_ids:
        rows = per_vehicle.get(vid, [])
        outcomes = [a.result.get('outcome') for a in rows]
        if not rows:
            state = 'not_assessed'
        elif MEETS_REQUIREMENTS in outcomes:
            state = MEETS_REQUIREMENTS
        elif INSUFFICIENT_EVIDENCE in outcomes:
            state = 'needs_evidence'
        else:
            state = NOT_SUITABLE
        approvable = any(
            a.result.get('outcome') == MEETS_REQUIREMENTS
            and a.status == 'completed'
            and a.evidence_level in APPROVAL_LEVELS for a in rows)
        states[vid] = {'state': state, 'models_assessed': len(rows),
                       'approvable': approvable,
                       'last_assessed_at': max(
                           (a.created_at for a in rows), default=None)}
    return states


def fleet_overview(org):
    vehicles = list(in_service(org).values('id', 'fuel_type'))
    by_fuel = defaultdict(int)
    for v in vehicles:
        by_fuel[v['fuel_type']] += 1
    ev = by_fuel.get(FuelType.EV, 0)
    ice = sum(by_fuel.get(f, 0) for f in ICE_FUELS)
    unclassified = len(vehicles) - ev - ice
    classified = ev + ice

    ice_ids = [v['id'] for v in vehicles if v['fuel_type'] in ICE_FUELS]
    states = vehicle_readiness_states(org, ice_ids)
    readiness = defaultdict(int)
    for s in states.values():
        readiness[s['state']] += 1
    approvable = sum(1 for s in states.values() if s['approvable'])

    decisions = defaultdict(int)
    latest_decision = {}
    for d in (MigrationDecision.objects.filter(organization=org,
                                               vehicle_id__in=ice_ids)
              .order_by('decided_at')):
        latest_decision[d.vehicle_id] = d.decision
    for d in latest_decision.values():
        decisions[d] += 1

    items = MigrationPlanItem.objects.filter(
        plan__organization=org).exclude(plan__status=PlanStatus.ARCHIVED)
    item_status = defaultdict(int)
    for st in items.values_list('status', flat=True):
        item_status[st] += 1
    target_plan = (MigrationPlan.objects
                   .filter(organization=org, status=PlanStatus.ACTIVE,
                           target_ev_share_pct__isnull=False)
                   .order_by('-updated_at').first())

    return {
        'method_version': METHOD,
        'composition': {
            'total': len(vehicles),
            'ev': ev,
            'ice': ice,
            'unclassified': unclassified,
            'by_fuel': dict(by_fuel),
            'ev_share_pct': round(100 * ev / classified, 1)
            if classified else None,
            'definition': (
                'Vehicles that are not retired or sold. EV = fuel type '
                '"ev"; ICE = diesel, petrol, CNG or LPG; fuel type "other" '
                'is unclassified and left out of the EV share.'),
        },
        'ice_readiness': {
            'vehicles': len(ice_ids),
            'meets_requirements': readiness[MEETS_REQUIREMENTS],
            'not_suitable': readiness[NOT_SUITABLE],
            'needs_evidence': readiness['needs_evidence'],
            'not_assessed': readiness['not_assessed'],
            'approvable': approvable,
            'definition': (
                'Each ICE vehicle counted once, from the latest readiness '
                'assessment for each EV model assessed against it. '
                '"Approvable" = meets every check with moderate or high '
                'evidence coverage, as formal approval requires.'),
        },
        'decisions': {
            'approve_transition': decisions[DecisionType.APPROVE_TRANSITION],
            'defer': decisions[DecisionType.DEFER],
            'reject': decisions[DecisionType.REJECT],
            'definition': 'Latest recorded decision per ICE vehicle.',
        },
        'plan_items': dict(item_status),
        'target': None if target_plan is None else {
            'plan_id': str(target_plan.pk),
            'plan_name': target_plan.name,
            'ev_share_pct': float(target_plan.target_ev_share_pct),
            'by': target_plan.end_date.isoformat()
            if target_plan.end_date else None,
        },
    }


def _months(end, n):
    y, m = end.year, end.month
    out = []
    for _ in range(n):
        out.append(date(y, m, 1))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def recorded_costs(org, months=12, powertrain=None):
    """Recorded fuel, charging and maintenance costs per calendar month.

    A month's value is the sum of records that carry a cost in the
    organization currency, or null when no such record exists. Records
    without a cost, or in another currency, are counted separately.
    """
    today, tz = S.today_for(org)
    buckets = _months(today, months)
    start = buckets[0]
    vehicles = in_service(org)
    if powertrain == 'ice':
        vehicles = vehicles.filter(fuel_type__in=ICE_FUELS)
    elif powertrain == 'ev':
        vehicles = vehicles.filter(fuel_type=FuelType.EV)
    vids = list(vehicles.values_list('id', flat=True))
    cur = org.currency

    series = {b: {'fuel': None, 'charging': None, 'maintenance': None}
              for b in buckets}
    stats = {k: {'records': 0, 'with_cost': 0, 'without_cost': 0,
                 'other_currency': 0, 'total': None, 'vehicles': set()}
             for k in ('fuel', 'charging', 'maintenance')}

    def add(cat, day, cost, currency, vid):
        st = stats[cat]
        st['records'] += 1
        if cost is None:
            st['without_cost'] += 1
            return
        if currency != cur:
            st['other_currency'] += 1
            return
        st['with_cost'] += 1
        st['vehicles'].add(vid)
        st['total'] = (st['total'] or 0) + float(cost)
        b = date(day.year, day.month, 1)
        series[b][cat] = (series[b][cat] or 0) + float(cost)

    for r in (EnergyRecord.objects
              .filter(organization=org, vehicle_id__in=vids,
                      voided_at__isnull=True)
              .filter(occurred_at__date__gte=start)):
        cat = 'fuel' if r.record_type == 'fuel_fill' else 'charging'
        add(cat, r.occurred_at.astimezone(tz).date(), r.cost, r.currency,
            r.vehicle_id)
    for e in MaintenanceEvent.objects.filter(
            organization=org, vehicle_id__in=vids, voided_at__isnull=True,
            occurred_on__gte=start):
        add('maintenance', e.occurred_on, e.cost, e.currency, e.vehicle_id)

    est = vehicles.filter(~Q(fuel_type=FuelType.EV))
    estimates = {}
    for field in ('fuel_cost_per_month', 'maintenance_cost_per_month'):
        vals = [v for v in est.values_list(field, flat=True)]
        known = [float(v) for v in vals if v is not None]
        estimates[field] = {
            'sum_per_month': round(sum(known), 2) if known else None,
            'vehicles_with_value': len(known),
            'vehicles_without_value': len(vals) - len(known),
        }

    return {
        'method_version': 'recorded-costs/1.0.0',
        'currency': cur,
        'powertrain': powertrain or 'all',
        'vehicles_in_scope': len(vids),
        'window': {'start': start.isoformat(), 'end': today.isoformat(),
                   'months': months},
        'months': [{'month': b.strftime('%Y-%m'),
                    **{k: (round(v, 2) if v is not None else None)
                       for k, v in series[b].items()}}
                   for b in buckets],
        'categories': {
            k: {**{x: v for x, v in st.items() if x != 'vehicles'},
                'total': round(st['total'], 2)
                if st['total'] is not None else None,
                'vehicles_with_cost': len(st['vehicles'])}
            for k, st in stats.items()},
        'vehicle_record_estimates': {
            **estimates,
            'note': ('Monthly figures typed on ICE vehicle records: '
                     'organization-provided, undated and unsourced. Shown '
                     'separately from recorded costs, never added to them.'),
        },
        'definition': (
            'Sums of non-voided fuel records, charging records and '
            'maintenance events that carry a cost in the organization '
            'currency. A month with no costed record is shown as no data, '
            'not zero. Partial records give a partial total.'),
    }
