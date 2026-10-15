"""Operational EV readiness screening - mandatory gates (plain Python).

Method ``ev-readiness/1.0.0``. Units: km, kWh, kWh/km, kg, kW, hours.

Formulas
--------
required_range_km      = design_day_distance_km * (1 + reserve_fraction)
battery_energy_needed  = required_range_km * c_battery
daily_grid_energy_kwh  = design_day_distance_km * c_grid
charging_hours         = daily_grid_energy_kwh / effective_power_kw
effective_power_kw     = min(charger_power_kw, vehicle_max_charge_kw)

where the candidate's consumption figure `c` is converted exactly once:

* basis "vehicle" (energy out of the battery):
    c_battery = c ;  c_grid = c / charging_efficiency
* basis "grid" (energy metered at the charger, losses already included):
    c_grid = c ;  c_battery = c * charging_efficiency
  If the efficiency is unknown, c_battery = c is used for the range gate.
  That over-states the battery energy needed (conservative) and is flagged.

Gates (all mandatory; any FAIL makes the candidate not suitable, whatever its
financial result; any INSUFFICIENT means no recommendation can be made):

range      battery_energy_needed <= usable_battery_kwh
payload    candidate_payload_kg >= required_payload_kg
charging   charging_hours <= charging_window_hours

Limitations stated in every result: constant charging power (no taper near
full), no charger sharing beyond the per-vehicle power entered, no
temperature/terrain/load adjustment beyond what the consumption figure
reflects. Manufacturer-certified range is never used.
"""

from evidence.errors import (
    CalculationError,
    require_fraction,
    require_non_negative,
    require_positive,
)

METHOD_VERSION = 'ev-readiness/1.0.0'

PASS = 'pass'
FAIL = 'fail'
INSUFFICIENT = 'insufficient'

MEETS_REQUIREMENTS = 'meets_requirements'
NOT_SUITABLE = 'not_suitable'
INSUFFICIENT_EVIDENCE = 'insufficient_evidence'

LIMITATIONS = [
    'Screening estimate: charging power is treated as constant (no taper '
    'near full charge).',
    'Charger contention is only reflected through the per-vehicle charger '
    'power entered for the depot.',
    'Weather, terrain, payload and HVAC effects are included only insofar as '
    'the consumption figure reflects them.',
    'Manufacturer-certified range is not used for any gate.',
]


def _r(x, p=3):
    return None if x is None else round(x, p)


def _check(name, status, explanation, **values):
    return {'check': name, 'status': status, 'explanation': explanation,
            'values': values}


def assess(v):
    """`v` is a dict of plain numbers/strings (None = not available)."""
    warnings = []

    d = v.get('design_day_distance_km')
    reserve = v.get('reserve_fraction')
    require_fraction('reserve_fraction', reserve)
    if d is not None:
        require_positive('design_day_distance_km', d)

    c = v.get('consumption_kwh_per_km')
    basis = v.get('consumption_basis')
    if c is not None:
        require_positive('consumption_kwh_per_km', c)
        if not 0.02 <= c <= 5:
            raise CalculationError(
                'consumption_kwh_per_km',
                f'{c} kWh/km is outside 0.02-5 kWh/km; check the unit.')
        if basis not in ('grid', 'vehicle'):
            raise CalculationError('consumption_basis',
                                   'must be "grid" or "vehicle".')
    eff = v.get('charging_efficiency')
    if eff is not None:
        require_fraction('charging_efficiency', eff, allow_zero=False)
        if eff < 0.5:
            raise CalculationError(
                'charging_efficiency',
                f'{eff} is implausibly low for grid-to-battery charging.')

    if c is None:
        c_battery = c_grid = None
    elif basis == 'vehicle':
        c_battery = c
        c_grid = c / eff if eff else None
    else:
        c_grid = c
        c_battery = c * eff if eff else c
        if not eff:
            warnings.append(
                'Charging efficiency unknown: the range gate uses grid-side '
                'consumption, which over-states the battery energy needed.')

    if v.get('consumption_source_type') == 'manufacturer_rated':
        warnings.append(
            'Consumption is a manufacturer-rated figure, not measured on this '
            'duty cycle; real-world consumption is often higher.')

    checks = []

    # ---- range ---------------------------------------------------------
    usable = v.get('usable_battery_kwh')
    if usable is not None:
        require_positive('usable_battery_kwh', usable)
    if d is None or c_battery is None or usable is None:
        missing = [n for n, x in (('design_day_distance_km', d),
                                  ('consumption_kwh_per_km', c),
                                  ('usable_battery_kwh', usable)) if x is None]
        checks.append(_check(
            'range', INSUFFICIENT,
            f'Cannot test range: missing {", ".join(missing)}.'))
        required_range = d * (1 + reserve) if d is not None else None
    else:
        required_range = d * (1 + reserve)
        needed = required_range * c_battery
        ok = needed <= usable
        checks.append(_check(
            'range', PASS if ok else FAIL,
            (f'Design day {d:g} km with {reserve:.0%} reserve needs '
             f'{required_range:.1f} km = {needed:.1f} kWh from the battery; '
             f'usable capacity is {usable:g} kWh.'),
            required_range_km=_r(required_range, 2),
            battery_energy_needed_kwh=_r(needed),
            usable_battery_kwh=usable,
            margin_kwh=_r(usable - needed),
            c_battery_kwh_per_km=_r(c_battery, 4),
        ))

    # ---- payload -------------------------------------------------------
    req_payload = v.get('required_payload_kg')
    cand_payload = v.get('candidate_payload_kg')
    if req_payload is not None:
        require_non_negative('required_payload_kg', req_payload)
    if cand_payload is not None:
        require_non_negative('candidate_payload_kg', cand_payload)
    if req_payload is None or cand_payload is None:
        checks.append(_check(
            'payload', INSUFFICIENT,
            'Cannot test payload: required and candidate payload are both '
            'needed.'))
    else:
        ok = cand_payload >= req_payload
        checks.append(_check(
            'payload', PASS if ok else FAIL,
            f'Candidate payload {cand_payload:g} kg vs required '
            f'{req_payload:g} kg.',
            required_payload_kg=req_payload,
            candidate_payload_kg=cand_payload,
            margin_kg=_r(cand_payload - req_payload, 1),
        ))

    # ---- charging --------------------------------------------------------
    charger = v.get('charger_power_kw')
    accept = v.get('vehicle_max_charge_kw')
    window = v.get('charging_window_hours')
    if charger is not None:
        require_positive('charger_power_kw', charger)
    if accept is not None:
        require_positive('vehicle_max_charge_kw', accept)
    if window is not None:
        require_positive('charging_window_hours', window)
        if window > 24:
            raise CalculationError('charging_window_hours',
                                   'cannot exceed 24 hours per day.')
    missing = [n for n, x in (
        ('design_day_distance_km', d),
        ('grid-side consumption (needs charging_efficiency for '
         'vehicle-basis figures)', c_grid),
        ('charger_power_kw', charger),
        ('vehicle_max_charge_kw for this connector', accept),
        ('charging_window_hours', window)) if x is None]
    if missing:
        checks.append(_check(
            'charging', INSUFFICIENT,
            f'Cannot test charging: missing {", ".join(missing)}. A charger '
            'existing at the depot is not, by itself, evidence that the '
            'energy can be delivered in time.'))
    else:
        power = min(charger, accept)
        grid_energy = d * c_grid
        hours = grid_energy / power
        ok = hours <= window
        checks.append(_check(
            'charging', PASS if ok else FAIL,
            (f'Replenishing {grid_energy:.1f} kWh from the grid at '
             f'{power:g} kW takes {hours:.2f} h; the window is {window:g} h.'),
            daily_grid_energy_kwh=_r(grid_energy),
            effective_power_kw=power,
            charging_hours=_r(hours),
            charging_window_hours=window,
            spare_hours=_r(window - hours),
            c_grid_kwh_per_km=_r(c_grid, 4),
        ))

    cur_type = v.get('current_vehicle_type')
    cand_type = v.get('candidate_vehicle_type')
    if cur_type and cand_type and cur_type != cand_type:
        warnings.append(
            f'Candidate is a {cand_type}; the current vehicle is a {cur_type}. '
            'Confirm it can perform the same duty.')

    statuses = {c['status'] for c in checks}
    if FAIL in statuses:
        overall = NOT_SUITABLE
    elif INSUFFICIENT in statuses:
        overall = INSUFFICIENT_EVIDENCE
    else:
        overall = MEETS_REQUIREMENTS

    return {
        'method_version': METHOD_VERSION,
        'outcome': overall,
        'eligible_for_scoring': overall == MEETS_REQUIREMENTS,
        'checks': checks,
        'derived': {
            'required_range_km': _r(required_range, 2),
            'c_battery_kwh_per_km': _r(c_battery, 4),
            'c_grid_kwh_per_km': _r(c_grid, 4),
        },
        'rated_range_km': v.get('rated_range_km'),
        'rated_range_note': ('Manufacturer-certified range, shown for '
                             'reference only. Not used in any gate.'),
        'warnings': warnings,
        'limitations': LIMITATIONS,
    }
