"""Total cost of ownership: ICE baseline vs EV candidate (plain Python).

Method ``tco/1.0.0``. Money is in the organization's currency; distance in
km; energy in L or kWh; time in years. All cash flows are costs (positive =
money out). Year 0 is the investment year; operating costs fall at the end of
years 1..N; residual value is received at the end of year N.

    NPC = I0 + sum_{t=1..N} C / (1 + r)^t  -  R / (1 + r)^N

Both scenarios share one annual distance, horizon and discount rate, so they
are compared on the same duty. Per scenario:

    ICE energy cost  = annual_km / km_per_l * fuel_price_per_l
    EV energy cost   = annual_km * grid_kwh_per_km * tariff_per_kwh
    EV I0            = purchase price + charging infrastructure
                       - verified incentives in force

Cost-boundary rule: insurance, fees, downtime cost and residual value are
optional categories. A category is included only when BOTH scenarios have a
value; if only one does, it is excluded from both and reported, so one side
is never charged for something the other side omits. Energy and maintenance
are mandatory for both. No missing value is ever treated as zero.

Derived figures:
    net_present_savings  = NPC_ice - NPC_ev   (positive favours the EV)
    levelised_cost_per_km = NPC / sum_{t=1..N} annual_km / (1 + r)^t
    operating_cost_per_km = annual operating cost / annual_km (undiscounted)
    simple_payback_years  = extra upfront / annual operating saving
        (only when both are positive; ignores discounting and residuals)
    discounted_payback_years = first point where the cumulative discounted
        saving (excluding residual values) covers the extra upfront cost,
        interpolated linearly within the year; null if not within N years.

Real vs nominal: the discount rate must match how the cost inputs are
expressed. Costs here are held flat in today's money, so a real discount rate
is the consistent choice; this is recorded, not enforced.
"""

from evidence.errors import (
    CalculationError,
    require_non_negative,
    require_positive,
)

METHOD_VERSION = 'tco/1.0.0'
OPTIONAL_ANNUAL = ('insurance_annual', 'fees_annual', 'downtime_annual')
MAX_HORIZON = 30


def _r2(x):
    return None if x is None else round(x, 2)


def _validate_common(i):
    km = i.get('annual_distance_km')
    n = i.get('horizon_years')
    r = i.get('discount_rate')
    if n is None or int(n) != n or not 1 <= n <= MAX_HORIZON:
        raise CalculationError('horizon_years',
                               f'must be a whole number from 1 to {MAX_HORIZON}.')
    if r is None or not 0 <= r < 1:
        raise CalculationError('discount_rate',
                               'must be a fraction from 0 to below 1 '
                               '(e.g. 0.08 for 8%).')
    if km is not None:
        require_positive('annual_distance_km', km)
    return km, int(n), r


def scenario(*, initial, annual, residual, km, n, r):
    """Cash flows and NPC for one scenario. `annual` is {category: cost}."""
    annual_total = sum(annual.values())
    flows = []
    npc = 0.0
    discounted_km = 0.0
    for t in range(0, n + 1):
        df = 1 / (1 + r) ** t
        cash = initial if t == 0 else annual_total
        res = residual if (t == n and residual is not None) else 0.0
        net = cash - res
        npc += net * df
        if t > 0:
            discounted_km += km * df
        flows.append({
            'year': t,
            'investment': _r2(initial if t == 0 else 0.0),
            'operating': _r2(annual_total if t > 0 else 0.0),
            'residual_credit': _r2(res),
            'net_cost': _r2(net),
            'discount_factor': round(df, 6),
            'discounted_net_cost': _r2(net * df),
        })
    return {
        'initial_investment': _r2(initial),
        'annual_costs': {k: _r2(v) for k, v in annual.items()},
        'annual_operating_total': _r2(annual_total),
        'residual_value': _r2(residual),
        'cash_flows': flows,
        'net_present_cost': _r2(npc),
        'levelised_cost_per_km': round(npc / discounted_km, 4),
        'operating_cost_per_km': round(annual_total / km, 4),
        '_npc': npc,
        '_annual_total': annual_total,
    }


def _payback(ice, ev, n, r):
    extra = ev['_initial'] - ice['_initial']
    saving = ice['_annual_total'] - ev['_annual_total']
    if extra <= 0:
        simple = 0.0  # nothing extra to recover
    elif saving > 0:
        simple = extra / saving
    else:
        simple = None

    cumulative = -extra
    discounted = 0.0 if cumulative >= 0 else None
    if discounted is None:
        for t in range(1, n + 1):
            step = saving / (1 + r) ** t
            if step > 0 and cumulative + step >= 0:
                discounted = (t - 1) + (-cumulative / step)
                break
            cumulative += step
    return {
        'extra_upfront_cost': _r2(extra),
        'annual_operating_saving': _r2(saving),
        'simple_payback_years': None if simple is None else round(simple, 2),
        'simple_payback_note': (
            'Undiscounted; ignores residual values. Not meaningful when the '
            'EV costs more to run each year.'),
        'discounted_payback_years': (
            None if discounted is None else round(discounted, 2)),
        'discounted_payback_note': (
            'Residual values excluded. Null means the extra upfront cost is '
            'not recovered within the horizon.'),
    }


MANDATORY = {
    'annual_distance_km': 'common',
    'ice_initial_investment': 'ice',
    'fuel_economy_km_per_l': 'ice',
    'fuel_price_per_l': 'ice',
    'ice_maintenance_annual': 'ice',
    'ev_purchase_price': 'ev',
    'charging_infrastructure_cost': 'ev',
    'ev_grid_kwh_per_km': 'ev',
    'electricity_tariff_per_kwh': 'ev',
    'ev_maintenance_annual': 'ev',
}


def compare(i):
    """Compare the ICE baseline with an EV candidate.

    Returns {'status': 'insufficient', 'missing': [...]} when a mandatory
    input is absent; raises CalculationError for impossible values.
    """
    km, n, r = _validate_common(i)
    missing = [k for k in MANDATORY if i.get(k) is None]
    if missing:
        return {'method_version': METHOD_VERSION, 'status': 'insufficient',
                'missing': missing}

    require_positive('fuel_economy_km_per_l', i['fuel_economy_km_per_l'])
    if i['fuel_economy_km_per_l'] > 100:
        raise CalculationError('fuel_economy_km_per_l',
                               'above 100 km/L is not plausible for a fleet '
                               'vehicle; check the unit.')
    require_positive('fuel_price_per_l', i['fuel_price_per_l'])
    require_positive('ev_grid_kwh_per_km', i['ev_grid_kwh_per_km'])
    require_positive('electricity_tariff_per_kwh',
                     i['electricity_tariff_per_kwh'])
    for k in ('ice_initial_investment', 'ice_maintenance_annual',
              'ev_purchase_price', 'charging_infrastructure_cost',
              'ev_maintenance_annual'):
        require_non_negative(k, i[k])

    notes = []
    excluded = []

    ice_annual = {
        'energy': km / i['fuel_economy_km_per_l'] * i['fuel_price_per_l'],
        'maintenance': i['ice_maintenance_annual'],
    }
    ev_annual = {
        'energy': km * i['ev_grid_kwh_per_km'] *
        i['electricity_tariff_per_kwh'],
        'maintenance': i['ev_maintenance_annual'],
    }
    for cat in OPTIONAL_ANNUAL:
        a, b = i.get(f'ice_{cat}'), i.get(f'ev_{cat}')
        if a is not None and b is not None:
            require_non_negative(f'ice_{cat}', a)
            require_non_negative(f'ev_{cat}', b)
            ice_annual[cat.replace('_annual', '')] = a
            ev_annual[cat.replace('_annual', '')] = b
        elif a is not None or b is not None:
            excluded.append(cat)
            notes.append(
                f'{cat.replace("_", " ")} provided for only one scenario; '
                'excluded from both to keep the cost boundary consistent.')

    ice_res, ev_res = i.get('ice_residual_value'), i.get('ev_residual_value')
    if ice_res is not None and ev_res is not None:
        require_non_negative('ice_residual_value', ice_res)
        require_non_negative('ev_residual_value', ev_res)
    else:
        if ice_res is not None or ev_res is not None:
            excluded.append('residual_value')
            notes.append('Residual value provided for only one scenario; '
                         'excluded from both.')
        ice_res = ev_res = None

    incentives_in = i.get('incentives') or []
    included_incentives = [x for x in incentives_in if x.get('included')]
    incentive_total = sum(x['amount'] for x in included_incentives)
    for x in incentives_in:
        if not x.get('included'):
            notes.append(f'Incentive "{x.get("name")}" not included: '
                         f'{x.get("reason")}')

    ev_initial = (i['ev_purchase_price'] + i['charging_infrastructure_cost']
                  - incentive_total)
    if ev_initial < 0:
        raise CalculationError('incentives',
                               'incentives exceed the EV investment; check '
                               'the incentive amounts.')
    ice_initial = i['ice_initial_investment']

    ice = scenario(initial=ice_initial, annual=ice_annual, residual=ice_res,
                   km=km, n=n, r=r)
    ev = scenario(initial=ev_initial, annual=ev_annual, residual=ev_res,
                  km=km, n=n, r=r)
    ice['_initial'], ev['_initial'] = ice_initial, ev_initial
    ev['investment_breakdown'] = {
        'purchase_price': _r2(i['ev_purchase_price']),
        'charging_infrastructure': _r2(i['charging_infrastructure_cost']),
        'incentives_applied': _r2(-incentive_total),
    }
    ice['energy_quantity'] = {
        'fuel_l_per_year': round(km / i['fuel_economy_km_per_l'], 2)}
    ev['energy_quantity'] = {
        'grid_kwh_per_year': round(km * i['ev_grid_kwh_per_km'], 2)}

    payback = _payback(ice, ev, n, r)
    nps = ice['_npc'] - ev['_npc']
    for s in (ice, ev):
        for k in ('_npc', '_annual_total', '_initial'):
            s.pop(k)

    return {
        'method_version': METHOD_VERSION,
        'status': 'ok',
        'common': {'annual_distance_km': km, 'horizon_years': n,
                   'discount_rate': r},
        'ice': ice,
        'ev': ev,
        'net_present_savings': _r2(nps),
        'net_present_savings_note': 'Positive values favour the EV.',
        **payback,
        'excluded_categories': excluded,
        'notes': notes,
    }


SENSITIVITY_KEYS = (
    'annual_distance_km', 'fuel_price_per_l', 'fuel_economy_km_per_l',
    'electricity_tariff_per_kwh', 'ev_grid_kwh_per_km', 'ev_purchase_price',
    'discount_rate',
)


def sensitivity(i, pct):
    """One-at-a-time +/- pct change of each key; reports NPS for each."""
    if not 0 < pct < 1:
        raise CalculationError('sensitivity_fraction',
                               'must be between 0 and 1 (e.g. 0.2 for 20%).')
    base = compare(i)
    if base['status'] != 'ok':
        return []
    rows = []
    for key in SENSITIVITY_KEYS:
        if i.get(key) is None:
            continue
        out = {'input': key, 'base_value': i[key],
               'base_net_present_savings': base['net_present_savings']}
        for label, factor in (('low', 1 - pct), ('high', 1 + pct)):
            changed = dict(i, **{key: i[key] * factor})
            try:
                res = compare(changed)
                out[f'{label}_value'] = round(changed[key], 6)
                out[f'{label}_net_present_savings'] = (
                    res.get('net_present_savings'))
            except CalculationError as exc:
                out[f'{label}_value'] = round(changed[key], 6)
                out[f'{label}_net_present_savings'] = None
                out[f'{label}_error'] = exc.message
        rows.append(out)
    return rows
