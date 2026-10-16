"""Fleet operational emissions from recorded activity (no persistence).

Method ``fleet-footprint/1.0.0``

    fuel kgCO2e        = litres in non-voided fuel records x fuel factor
                         (factor per fuel type and boundary)
    electricity kgCO2e = grid-side kWh in non-voided charging records x
                         grid factor; vehicle-side kWh are divided by the
                         recorded charging efficiency, or left out

Factors are the organization's recorded emission-factor observations in force
at the end of the reporting period; the same factors are applied to every
month and to the previous period, so comparisons use one method. Records
that cannot be converted (no factor for the fuel, unknown efficiency) are
counted and reported, never treated as zero emissions. Vehicles without
records in the period are not part of the figure. Vehicle and battery
manufacturing (lifecycle) emissions are never included.

Method ``electrification-scenario/1.0.0``

    For each ICE vehicle with recorded fuel in the period and an EV model
    that met every readiness check for it:
      distance  = odometer increase within the period (measured), else
                  litres x fuel economy (fill-to-fill, else vehicle record)
      EV kWh    = distance x EV model consumption (grid side)
      EV kgCO2e = EV kWh x grid factor
    scenario(p) = current - p x sum(ICE kgCO2e - EV kgCO2e) over those
    vehicles: p of their recorded activity is assumed to move to the
    assessed EV model. A modelled estimate, not a measured outcome.
"""

from collections import defaultdict
from datetime import date, datetime, time, timedelta

from evidence import services as S
from evidence.models import Assessment, EnergyRecord
from ev_migration.calc.emissions import COMPARABILITY
from ev_migration.overview import ICE_FUELS, fleet_overview, in_service

FOOTPRINT_METHOD = 'fleet-footprint/1.0.0'
SCENARIO_METHOD = 'electrification-scenario/1.0.0'
FACTOR_FUELS = ('diesel', 'petrol')
SCENARIO_SHARES = (25, 50, 75, 100)

BOUNDARY_LABEL = {
    ('tank_to_wheel', 'generation'):
        'Tailpipe (fuel combustion) + electricity generation',
    ('tank_to_wheel', 'consumption_incl_td_losses'):
        'Tailpipe (fuel combustion) + electricity delivered incl. losses',
    ('well_to_wheel', 'consumption_incl_td_losses'):
        'Well-to-wheel (fuel and electricity supply chains included)',
    ('well_to_wheel', 'generation'):
        'Well-to-wheel fuel + electricity generation',
}


def _f(v):
    return None if v is None else float(v)


def _factor_ref(obs):
    if obs is None:
        return None
    return {
        'observation_id': str(obs.pk), 'value': float(obs.value),
        'unit': obs.unit, 'qualifiers': obs.qualifiers,
        'effective_from': obs.effective_from.isoformat()
        if obs.effective_from else None,
        'source': obs.source.as_reference() if obs.source else None,
        'origin': obs.origin,
    }


def factors_for(org, as_of, fuel_boundary, grid_boundary):
    fuel = {f: S.latest_observation(
        org, 'fuel_emission_factor_kgco2e_per_l', as_of,
        organization_level=True,
        qualifiers={'fuel': f, 'boundary': fuel_boundary})
        for f in FACTOR_FUELS}
    grid = S.latest_observation(
        org, 'grid_emission_factor_kgco2e_per_kwh', as_of,
        organization_level=True, qualifiers={'boundary': grid_boundary})
    eff = S.latest_observation(org, 'charging_efficiency_fraction', as_of,
                               organization_level=True)
    return fuel, grid, eff


def available_boundaries(org, as_of):
    out = []
    for (fb, gb), (_, explanation) in COMPARABILITY.items():
        fuel, grid, _ = factors_for(org, as_of, fb, gb)
        out.append({
            'fuel_boundary': fb, 'grid_boundary': gb,
            'label': BOUNDARY_LABEL[(fb, gb)], 'explanation': explanation,
            'fuel_factors': sorted(f for f, o in fuel.items() if o),
            'grid_factor': grid is not None,
        })
    return out


def _bounds(org, start, end):
    _, tz = S.today_for(org)
    lo = datetime.combine(start, time.min, tzinfo=tz)
    hi = datetime.combine(end, time.max, tzinfo=tz)
    return lo, hi, tz


def _month_keys(start, end):
    keys, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        keys.append(f'{y:04d}-{m:02d}')
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return keys


def _activity(org, start, end, fuel, grid, eff, vehicles):
    """Convert one period's records with fixed factors."""
    lo, hi, tz = _bounds(org, start, end)
    by_id = {v.pk: v for v in vehicles}
    months = {k: {'fuel': None, 'electricity': None}
              for k in _month_keys(start, end)}
    per_vehicle = defaultdict(lambda: {'kg': 0.0, 'litres': 0.0, 'kwh': 0.0})
    excluded = defaultdict(int)
    counted = 0
    reporting = set()
    totals = {'fuel': None, 'electricity': None, 'litres': 0.0, 'kwh': 0.0}

    def add(cat, month, kg):
        months[month][cat] = (months[month][cat] or 0.0) + kg
        totals[cat] = (totals[cat] or 0.0) + kg

    for r in EnergyRecord.objects.filter(
            organization=org, vehicle_id__in=by_id, voided_at__isnull=True,
            occurred_at__gte=lo, occurred_at__lte=hi):
        v = by_id[r.vehicle_id]
        reporting.add(v.pk)
        month = r.occurred_at.astimezone(tz).strftime('%Y-%m')
        qty = float(r.quantity)
        if r.record_type == 'fuel_fill':
            factor = fuel.get(v.fuel_type)
            if v.fuel_type not in FACTOR_FUELS:
                excluded[f'{v.fuel_type.upper()} fuel: EVORA has no emission '
                         'factor for this fuel yet'] += 1
                continue
            if factor is None:
                excluded[f'{v.fuel_type} fuel: no {v.fuel_type} emission '
                         'factor recorded for this boundary'] += 1
                continue
            kg = qty * float(factor.value)
            add('fuel', month, kg)
            totals['litres'] += qty
            per_vehicle[v.pk]['litres'] += qty
        else:
            if grid is None:
                excluded['charging: no grid emission factor recorded for '
                         'this boundary'] += 1
                continue
            kwh = qty
            if r.energy_basis == 'vehicle':
                if eff is None:
                    excluded['charging reported by the vehicle: charging '
                             'efficiency not recorded'] += 1
                    continue
                kwh = qty / float(eff.value)
            kg = kwh * float(grid.value)
            add('electricity', month, kg)
            totals['kwh'] += kwh
            per_vehicle[v.pk]['kwh'] += kwh
        per_vehicle[v.pk]['kg'] += kg
        counted += 1

    any_counted = counted > 0
    total = None
    if any_counted:
        total = (totals['fuel'] or 0.0) + (totals['electricity'] or 0.0)
    return {
        'total': total, 'fuel': totals['fuel'],
        'electricity': totals['electricity'], 'litres': totals['litres'],
        'kwh': totals['kwh'], 'records_counted': counted,
        'records_excluded': [{'reason': k, 'records': n}
                             for k, n in sorted(excluded.items())],
        'reporting': reporting, 'per_vehicle': per_vehicle,
        'months': months,
    }


def _r(v, d=1):
    return None if v is None else round(v, d)


def _distance(org, v, start, end, litres):
    """(km, basis, note) for the scenario; None when not determinable."""
    lo, hi, tz = _bounds(org, start, end)
    readings = sorted(
        float(o.value) for o in S.current_observations(org).filter(
            kind='odometer_km', vehicle=v, observed_at__gte=lo,
            observed_at__lte=hi, validation_status='valid'))
    if len(readings) >= 2 and readings[-1] > readings[0]:
        return (readings[-1] - readings[0], 'measured',
                'odometer increase within the period')
    usage = S.vehicle_usage(org, v, lo, hi, tz)
    fuel = usage['measured']['fuel_consumption']
    if fuel.get('status') == 'ok' and fuel.get('km_per_l'):
        return (litres * fuel['km_per_l'], 'measured',
                f'recorded litres x {fuel["km_per_l"]:.2f} km/L (fill-to-fill)')
    if v.mileage_kmpl:
        return (litres * float(v.mileage_kmpl), 'organization_provided',
                f'recorded litres x {float(v.mileage_kmpl):g} km/L '
                '(vehicle record)')
    return None


def _scenario(org, start, end, act, grid, as_of, vehicles):
    if grid is None:
        return {'method_version': SCENARIO_METHOD, 'status': 'insufficient',
                'reason': 'No grid emission factor is recorded for this '
                          'boundary, so EV electricity emissions cannot be '
                          'modelled.', 'vehicles': [], 'shares': []}
    ice = [v for v in vehicles if v.fuel_type in ICE_FUELS]
    latest = {}
    for a in (Assessment.objects
              .filter(organization=org, kind='ev_readiness',
                      vehicle_id__in=[v.pk for v in ice])
              .select_related('ev_candidate').order_by('created_at')):
        if a.result.get('outcome') == 'meets_requirements' and a.ev_candidate:
            latest[a.vehicle_id] = a.ev_candidate
    rows, skipped = [], defaultdict(int)
    for v in ice:
        pv = act['per_vehicle'].get(v.pk)
        if not pv or pv['litres'] <= 0:
            continue
        cand = latest.get(v.pk)
        if cand is None:
            skipped['no EV model has met every readiness check yet'] += 1
            continue
        dist = _distance(org, v, start, end, pv['litres'])
        if dist is None:
            skipped['distance driven could not be determined'] += 1
            continue
        c = float(cand.energy_consumption_kwh_per_km)
        if cand.consumption_basis == 'vehicle':
            eff = (S.latest_observation(org, 'charging_efficiency_fraction',
                                        as_of, ev_candidate=cand)
                   or (S.latest_observation(
                       org, 'charging_efficiency_fraction', as_of,
                       depot=v.depot) if v.depot_id else None)
                   or S.latest_observation(
                       org, 'charging_efficiency_fraction', as_of,
                       organization_level=True))
            if eff is None:
                skipped['charging efficiency not recorded'] += 1
                continue
            c = c / float(eff.value)
        km, basis, note = dist
        ev_kwh = km * c
        ev_kg = ev_kwh * float(grid.value)
        rows.append({
            'vehicle_id': str(v.pk), 'registration_number':
            v.registration_number, 'distance_km': _r(km),
            'distance_basis': basis, 'distance_note': note,
            'litres': _r(pv['litres'], 2), 'ice_kgco2e': _r(pv['kg']),
            'ev_model': cand.label(),
            'ev_consumption_source': cand.consumption_source_type,
            'ev_grid_kwh_per_km': round(c, 4), 'ev_kwh': _r(ev_kwh),
            'ev_kgco2e': _r(ev_kg), 'reduction_kgco2e': _r(pv['kg'] - ev_kg),
        })
    if not rows:
        return {'method_version': SCENARIO_METHOD, 'status': 'insufficient',
                'reason': 'No ICE vehicle with recorded fuel in this period '
                          'has an EV model that met every readiness check. '
                          'Check vehicles in Discover & Compare first.',
                'skipped': [{'reason': k, 'vehicles': n}
                            for k, n in skipped.items()],
                'vehicles': [], 'shares': []}
    potential = sum(r['ice_kgco2e'] - r['ev_kgco2e'] for r in rows)
    current = act['total']
    shares = []
    for p in SCENARIO_SHARES:
        modelled = current - potential * p / 100
        shares.append({
            'share_pct': p, 'modelled_kgco2e': _r(modelled),
            'reduction_kgco2e': _r(potential * p / 100),
            'reduction_pct': _r(100 * potential * p / 100 / current)
            if current else None,
        })
    return {
        'method_version': SCENARIO_METHOD, 'status': 'ok',
        'eligible_vehicles': len(rows), 'ice_vehicles': len(ice),
        'vehicles': rows, 'shares': shares,
        'skipped': [{'reason': k, 'vehicles': n} for k, n in skipped.items()],
        'grid_factor': _factor_ref(grid),
        'definition': (
            'Modelled estimate. For each eligible vehicle the recorded '
            'activity of the period is replayed on the EV model that met '
            'every readiness check for it, with grid emissions for the '
            'electricity. A share p assumes p of that activity moves to the '
            'EV. Not a measured outcome or a guarantee.'),
    }


def footprint(org, start, end, fuel_boundary, grid_boundary):
    if (fuel_boundary, grid_boundary) not in COMPARABILITY:
        raise ValueError('Unknown boundary pair.')
    vehicles = list(in_service(org))
    fuel, grid, eff = factors_for(org, end, fuel_boundary, grid_boundary)
    act = _activity(org, start, end, fuel, grid, eff, vehicles)
    days = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=days - 1)
    prev = _activity(org, prev_start, prev_end, fuel, grid, eff, vehicles)

    reasons = []
    if act['total'] is None or prev['total'] is None:
        reasons.append('No converted records in one of the two periods.')
    elif act['reporting'] != prev['reporting']:
        only_now = len(act['reporting'] - prev['reporting'])
        only_then = len(prev['reporting'] - act['reporting'])
        reasons.append(
            f'Different vehicles reported: {only_now} only in this period, '
            f'{only_then} only in the previous one.')
    comparable = not reasons
    change = (act['total'] - prev['total']) if comparable else None

    ice_ids = {v.pk for v in vehicles if v.fuel_type in ICE_FUELS}
    ev_ids = {v.pk for v in vehicles if v.fuel_type == 'ev'}
    comp = fleet_overview(org)['composition']
    label, explanation = COMPARABILITY[(fuel_boundary, grid_boundary)]
    missing = []
    if all(f is None for f in fuel.values()):
        missing.append('fuel_emission_factor_kgco2e_per_l')
    if grid is None:
        missing.append('grid_emission_factor_kgco2e_per_kwh')

    months = [{'month': k, 'fuel_kgco2e': _r(m['fuel']),
               'electricity_kgco2e': _r(m['electricity']),
               'kgco2e': _r((m['fuel'] or 0) + (m['electricity'] or 0))
               if m['fuel'] is not None or m['electricity'] is not None
               else None}
              for k, m in act['months'].items()]

    return {
        'method_version': FOOTPRINT_METHOD,
        'status': 'ok' if act['total'] is not None else 'insufficient',
        'period': {'start': start.isoformat(), 'end': end.isoformat(),
                   'days': days},
        'boundary': {
            'fuel': fuel_boundary, 'grid': grid_boundary,
            'label': BOUNDARY_LABEL[(fuel_boundary, grid_boundary)],
            'comparability': label, 'explanation': explanation,
            'lifecycle_included': False,
        },
        'unit': 'kgCO2e',
        'factors': {
            'fuel': {f: _factor_ref(o) for f, o in fuel.items()},
            'grid': _factor_ref(grid),
            'charging_efficiency': _factor_ref(eff),
            'as_of': end.isoformat(),
        },
        'missing': missing,
        'totals': {
            'kgco2e': _r(act['total']), 'fuel_kgco2e': _r(act['fuel']),
            'electricity_kgco2e': _r(act['electricity']),
            'fuel_litres': _r(act['litres'], 1),
            'electricity_kwh': _r(act['kwh'], 1),
        },
        'coverage': {
            'vehicles_in_service': len(vehicles),
            'ice_vehicles': len(ice_ids), 'ev_vehicles': len(ev_ids),
            'reporting_vehicles': len(act['reporting']),
            'ice_reporting': len(act['reporting'] & ice_ids),
            'ev_reporting': len(act['reporting'] & ev_ids),
            'records_counted': act['records_counted'],
            'records_excluded': act['records_excluded'],
        },
        'months': months,
        'previous': {
            'period': {'start': prev_start.isoformat(),
                       'end': prev_end.isoformat()},
            'kgco2e': _r(prev['total']), 'comparable': comparable,
            'reasons': reasons, 'change_kgco2e': _r(change),
            'change_pct': _r(100 * change / prev['total'])
            if comparable and prev['total'] else None,
            'note': 'Same factors and boundary as the current period.',
        },
        'composition': comp,
        'scenario': _scenario(org, start, end, act, grid, end, vehicles)
        if act['total'] is not None else {
            'method_version': SCENARIO_METHOD, 'status': 'insufficient',
            'reason': 'A current footprint is needed before a scenario can '
                      'be modelled.', 'vehicles': [], 'shares': []},
        'definition': (
            'Operational emissions from fuel and charging records of the '
            'period, using your recorded emission factors. Vehicles without '
            'records are not included, records that cannot be converted are '
            'listed, and vehicle or battery manufacturing (lifecycle) '
            'emissions are not included.'),
    }


def parse_period(params, org):
    """start/end (ISO dates, inclusive) with a 3-year limit; defaults to the
    last 12 full months plus the current month."""
    today, _ = S.today_for(org)
    try:
        end = date.fromisoformat(params.get('end')) if params.get('end') \
            else today
        start = date.fromisoformat(params.get('start')) \
            if params.get('start') else date(today.year - 1, today.month, 1)
    except ValueError:
        raise ValueError('Dates must be YYYY-MM-DD.')
    if end > today:
        end = today
    if start > end:
        raise ValueError('start must be on or before end.')
    if (end - start).days > 3 * 366:
        raise ValueError('The period can be at most three years.')
    return start, end
