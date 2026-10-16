"""Operational emissions: ICE baseline vs EV candidate (plain Python).

Method ``emissions/1.0.0``. Units: km/year, L/year, kWh/year, kgCO2e.

    baseline_fuel_l     = annual_km / km_per_l
    baseline_kgco2e     = baseline_fuel_l * fuel_factor_kgco2e_per_l
    ev_grid_kwh         = annual_km * grid_kwh_per_km
    ev_kgco2e           = ev_grid_kwh * grid_factor_kgco2e_per_kwh

Boundaries are carried with the factors and reported, never assumed:

fuel  tank_to_wheel   combustion (tailpipe) only
      well_to_wheel   combustion + fuel production and distribution
grid  generation      emissions at the power station per kWh generated
      consumption_incl_td_losses   per kWh delivered, incl. network losses

An EV has zero *tailpipe* emissions; the EV figure here is the emissions of
generating its electricity. Vehicle and battery manufacturing (lifecycle
emissions) are NOT included: no lifecycle data or method is configured.
"""

from evidence.errors import CalculationError, require_positive

METHOD_VERSION = 'emissions/1.0.0'

COMPARABILITY = {
    ('tank_to_wheel', 'generation'): (
        'operational',
        'ICE tailpipe vs EV electricity generation. Fuel production upstream '
        'is excluded from the ICE side, which favours the ICE baseline.'),
    ('tank_to_wheel', 'consumption_incl_td_losses'): (
        'operational',
        'ICE tailpipe vs EV electricity delivered (incl. network losses). '
        'Fuel production upstream is excluded from the ICE side.'),
    ('well_to_wheel', 'consumption_incl_td_losses'): (
        'well_to_wheel',
        'Both sides include energy supply-chain emissions; the closest '
        'like-for-like comparison available here.'),
    ('well_to_wheel', 'generation'): (
        'mixed',
        'ICE includes fuel supply chain; EV excludes network losses. The '
        'comparison slightly favours the EV.'),
}


def compare(i):
    required = ('annual_distance_km', 'fuel_economy_km_per_l',
                'fuel_factor_kgco2e_per_l', 'ev_grid_kwh_per_km',
                'grid_factor_kgco2e_per_kwh')
    missing = [k for k in required if i.get(k) is None]
    for k in ('fuel_boundary', 'grid_boundary'):
        if not i.get(k):
            missing.append(k)
    if missing:
        return {'method_version': METHOD_VERSION, 'status': 'insufficient',
                'missing': missing}

    km = require_positive('annual_distance_km', i['annual_distance_km'])
    kmpl = require_positive('fuel_economy_km_per_l',
                            i['fuel_economy_km_per_l'])
    ff = require_positive('fuel_factor_kgco2e_per_l',
                          i['fuel_factor_kgco2e_per_l'])
    c_grid = require_positive('ev_grid_kwh_per_km', i['ev_grid_kwh_per_km'])
    gf = i['grid_factor_kgco2e_per_kwh']
    if gf < 0:
        raise CalculationError('grid_factor_kgco2e_per_kwh',
                               'must not be negative.')
    key = (i['fuel_boundary'], i['grid_boundary'])
    if key not in COMPARABILITY:
        raise CalculationError('boundary', f'Unknown boundary pair {key}.')
    label, explanation = COMPARABILITY[key]

    fuel_l = km / kmpl
    base = fuel_l * ff
    grid_kwh = km * c_grid
    ev = grid_kwh * gf
    diff = base - ev
    return {
        'method_version': METHOD_VERSION,
        'status': 'ok',
        'annual_distance_km': km,
        'baseline': {
            'fuel_l_per_year': round(fuel_l, 2),
            'factor_kgco2e_per_l': ff,
            'boundary': i['fuel_boundary'],
            'kgco2e_per_year': round(base, 1),
        },
        'ev': {
            'grid_kwh_per_year': round(grid_kwh, 2),
            'factor_kgco2e_per_kwh': gf,
            'boundary': i['grid_boundary'],
            'kgco2e_per_year': round(ev, 1),
            'tailpipe_kgco2e_per_year': 0.0,
            'note': ('Electricity-generation emissions. Zero only applies to '
                     'tailpipe emissions.'),
        },
        'reduction_kgco2e_per_year': round(diff, 1),
        'reduction_fraction': round(diff / base, 4) if base > 0 else None,
        'comparison_boundary': label,
        'comparison_note': explanation,
        'excluded': ['vehicle and battery manufacturing (lifecycle)',
                     'end-of-life'],
    }
