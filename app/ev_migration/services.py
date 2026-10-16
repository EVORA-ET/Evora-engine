"""EV Migration: resolve evidence -> snapshot -> calculate -> persist.

Resolution order for every input is fixed and recorded in the snapshot. An
input supplied in the request (an observation reference, or an inline value
labelled organization_provided/assumed) always wins; otherwise:

annual_distance_km      measured odometer (annualised) -> vehicle observation
                        `annual_distance_km` -> vehicle record avg_monthly_km x 12
fuel_economy_km_per_l   measured fuel records (fill-to-fill) -> vehicle
                        observation -> vehicle record mileage_kmpl (if > 0)
fuel_price_per_l        depot observation -> organization observation
                        (qualifier fuel = the vehicle's fuel type)
ice_maintenance_annual  vehicle observation -> vehicle record
                        maintenance_cost_per_month x 12
electricity_tariff      depot observation -> organization observation
charging_efficiency     candidate -> depot -> organization observation
charger_power_kw        depot observation (qualifier connector)
charging_window_hours   vehicle observation -> depot observation
required_payload_kg     vehicle observation -> vehicle record payload_kg
                        (like-for-like capacity, labelled)
candidate prices etc.   candidate observations
grid/fuel factors       depot/organization observations with the requested
                        boundary

The vehicle-record fallbacks are the legacy single-value fields; they resolve
as organization_provided with the transformation stated, which caps evidence
coverage at "moderate".
"""

from rest_framework.exceptions import ValidationError

from evidence import assessments, provenance as P
from evidence import services as S
from evidence.models import AssessmentKind
from ev_migration.calc import emissions as emissions_calc
from ev_migration.calc import readiness as readiness_calc
from ev_migration.calc import tco as tco_calc
from vehicles.models import FuelType

SUPPORTED_FUELS = ('diesel', 'petrol')


def _vehicle_identity(vehicle):
    return {
        'vehicle_id': str(vehicle.pk),
        'registration_number': vehicle.registration_number,
        'manufacturer': vehicle.manufacturer,
        'model': vehicle.model,
        'vehicle_type': vehicle.vehicle_type,
        'fuel_type': vehicle.fuel_type,
        'depot_id': str(vehicle.depot_id) if vehicle.depot_id else None,
    }


def _candidate_identity(c):
    return {
        'ev_candidate_id': str(c.pk), 'label': c.label(),
        'vehicle_type': c.vehicle_type,
        'source_id': str(c.source_id),
    }


def _require_ice(vehicle):
    if vehicle.fuel_type == FuelType.EV:
        raise ValidationError(
            {'vehicle_id': 'This vehicle is already electric; migration '
             'assessments compare a combustion vehicle with an EV candidate.'})


def _require_supported_fuel(vehicle):
    if vehicle.fuel_type not in SUPPORTED_FUELS:
        raise ValidationError({'vehicle_id': (
            f'Fuel type "{vehicle.fuel_type}" is not supported yet: fuel '
            'prices, economy and emission factors are defined per litre for '
            'diesel and petrol only. CNG/LPG need per-kg kinds first.')})


class Ctx:
    """Shared resolution context for one request."""

    def __init__(self, org, vehicle, candidate, body, as_of, tz):
        self.org, self.vehicle, self.candidate = org, vehicle, candidate
        self.body, self.as_of, self.tz = body or {}, as_of, tz
        self.currency = org.currency
        self._usage = None
        self.window_days = body.get('usage_window_days', 365)
        if not isinstance(self.window_days, int) or not \
                30 <= self.window_days <= 1095:
            raise ValidationError(
                {'usage_window_days': 'Whole number of days from 30 to 1095.'})

    def unit(self, kind):
        from evidence.kinds import KINDS
        return KINDS[kind].unit_for(self.currency)

    def explicit(self, name, kind, critical=True, subjects=None):
        spec = self.body.get('inputs', {}).get(name)
        if spec is None:
            return None
        return lambda: S.resolve_explicit(
            self.org, name, spec, kind_key=kind, unit=self.unit(kind),
            as_of=self.as_of, critical=critical, allowed_subjects=subjects)

    def obs(self, kind, **where):
        def attempt():
            o = S.latest_observation(self.org, kind, self.as_of, **where)
            return S.resolved_from_observation(o, kind, self.as_of) if o \
                else None
        return attempt

    def usage(self):
        if self._usage is None:
            from datetime import timedelta
            from django.utils import timezone
            end = timezone.now()
            self._usage = S.vehicle_usage(
                self.org, self.vehicle, end - timedelta(days=self.window_days),
                end, self.tz)
        return self._usage

    def legacy(self, name, value, unit, transformation):
        def attempt():
            if value is None or float(value) <= 0:
                return None
            return P.ResolvedInput(
                name=name, value=float(value), unit=unit,
                origin=P.ORGANIZATION_PROVIDED,
                evidence_class=P.ORGANIZATION_PROVIDED,
                transformation=transformation,
                note='Legacy vehicle-record field: no date or source stored.')
        return attempt


def _inputs(*resolved):
    return {r.name: r.to_dict() for r in resolved}


def _measured_from_usage(ctx, name, unit, value, transformation, ids):
    """A value EVORA derived from measured records."""
    if value is None:
        return None
    usage = ctx.usage()['measured']['odometer']
    last = usage.get('last_reading') or {}
    stale, reason = False, ''
    if last.get('at'):
        from evidence.kinds import KINDS
        age = (ctx.as_of - S.as_date(last['at'][:10])).days
        if age > KINDS['odometer_km'].max_age_days:
            stale = True
            reason = f'latest odometer reading is {age} days old'
    return P.ResolvedInput(
        name=name, value=float(value), unit=unit, origin=P.MEASURED,
        evidence_class=P.STALE if stale else P.MEASURED, stale=stale,
        stale_reason=reason, transformation=transformation,
        note=f'Derived from {len(ids)} stored record(s).',
        derived_from_ids=list(ids))


# ---------------------------------------------------------------------------
# Shared resolvers
# ---------------------------------------------------------------------------


def resolve_annual_distance(ctx):
    v = ctx.vehicle

    def measured():
        odo = ctx.usage()['measured']['odometer']
        if odo['status'] != 'ok' or odo['annualised_distance_km'] is None:
            return None
        return _measured_from_usage(
            ctx, 'annual_distance_km', 'km/year',
            odo['annualised_distance_km'],
            (f'({odo["last_reading"]["km"]} - {odo["first_reading"]["km"]}) km '
             f'over {odo["span_days"]} days x 365 (usage/1.0.0)'),
            odo['reading_ids'])

    return S.first_available(
        'annual_distance_km', 'km/year',
        *(a for a in (ctx.explicit('annual_distance_km', 'annual_distance_km'),
                      measured,
                      ctx.obs('annual_distance_km', vehicle=v),
                      _scale(ctx.legacy('annual_distance_km',
                                        v.avg_monthly_km, 'km/year',
                                        'vehicle.avg_monthly_km x 12'), 12))
          if a is not None),
        missing_note='No odometer history, annual distance observation or '
                     'average monthly distance on the vehicle record.')


def _scale(attempt, factor):
    def wrapped():
        r = attempt()
        if r is not None and r.present:
            r.value = round(r.value * factor, 6)
        return r
    return wrapped


def resolve_fuel_economy(ctx):
    v = ctx.vehicle

    def measured():
        fuel = ctx.usage()['measured']['fuel_consumption']
        if fuel.get('status') != 'ok':
            return None
        return _measured_from_usage(
            ctx, 'fuel_economy_km_per_l', 'km/L', fuel['km_per_l'],
            (f'{fuel["distance_km"]} km / {fuel["fuel_l"]} L between full '
             'fills (fill-to-fill, usage/1.0.0)'), fuel['record_ids'])

    return S.first_available(
        'fuel_economy_km_per_l', 'km/L',
        *(a for a in (
            ctx.explicit('fuel_economy_km_per_l', 'fuel_economy_km_per_l'),
            measured,
            ctx.obs('fuel_economy_km_per_l', vehicle=v),
            ctx.legacy('fuel_economy_km_per_l', v.mileage_kmpl, 'km/L',
                       'vehicle.mileage_kmpl as entered')) if a is not None),
        missing_note='No fuel records, fuel economy observation or usable '
                     'mileage on the vehicle record.')


def resolve_ev_grid_consumption(ctx):
    """(c_grid ResolvedInput, efficiency ResolvedInput, consumption input)."""
    c = ctx.candidate
    consumption = S.resolved_from_record(
        'candidate_consumption_kwh_per_km', c.energy_consumption_kwh_per_km,
        'kWh/km', origin=c.origin, source=c.source, as_of=ctx.as_of,
        transformation=(f'EV candidate spec ({c.consumption_source_type}, '
                        f'{c.consumption_basis}-side)'))
    eff = resolve_efficiency(ctx, critical=c.consumption_basis == 'vehicle')
    if c.consumption_basis == 'grid':
        grid = S.combine('ev_grid_kwh_per_km', consumption.value, 'kWh/km',
                         [consumption], 'Candidate consumption is already '
                         'grid-side; used as is.')
    else:
        value = (consumption.value / eff.value) if eff.present else None
        grid = S.combine('ev_grid_kwh_per_km',
                         None if value is None else round(value, 6), 'kWh/km',
                         [consumption, eff],
                         'vehicle-side consumption / charging efficiency')
    return grid, eff, consumption


def resolve_efficiency(ctx, critical):
    k = 'charging_efficiency_fraction'
    attempts = [ctx.explicit('charging_efficiency', k),
                ctx.obs(k, ev_candidate=ctx.candidate)]
    if ctx.vehicle.depot_id:
        attempts.append(ctx.obs(k, depot=ctx.vehicle.depot))
    attempts.append(ctx.obs(k, organization_level=True))
    return S.first_available('charging_efficiency', 'fraction',
                             *(a for a in attempts if a), critical=critical)


def _depot_then_org(ctx, name, kind, qualifiers=None, critical=True):
    attempts = [ctx.explicit(name, kind)]
    if ctx.vehicle.depot_id:
        attempts.append(ctx.obs(kind, depot=ctx.vehicle.depot,
                                qualifiers=qualifiers))
    attempts.append(ctx.obs(kind, organization_level=True,
                            qualifiers=qualifiers))
    return S.first_available(name, ctx.unit(kind),
                             *(a for a in attempts if a), critical=critical)


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------


def _design_day(ctx):
    spec = ctx.body.get('design_day_distance')
    if not isinstance(spec, dict):
        raise ValidationError({'design_day_distance': (
            'Required. Use {"basis": "measured_p90" | "measured_max" | '
            '"planned_max"} or an inline {"value", "unit": "km", "origin", '
            '"note"}.')})
    basis = spec.get('basis')
    if basis is None:
        return S.resolve_explicit(ctx.org, 'design_day_distance_km', spec,
                                  kind_key=None, unit='km', as_of=ctx.as_of)
    usage = ctx.usage()
    if basis in ('measured_p90', 'measured_max'):
        odo = usage['measured']['odometer']
        key = 'p90_km' if basis == 'measured_p90' else 'max_observed_km'
        value = odo['daily'][key] if odo['status'] == 'ok' else None
        r = _measured_from_usage(
            ctx, 'design_day_distance_km', 'km', value,
            (f'{"90th percentile" if key == "p90_km" else "maximum"} of '
             f'{odo["daily"]["sample_count"]} measured daily distances, '
             f'{ctx.window_days}-day window (usage/1.0.0)'),
            odo.get('reading_ids', []))
        if r is None:
            return P.ResolvedInput.missing(
                'design_day_distance_km', 'km',
                note=('Not enough measured daily distances: '
                      + '; '.join(odo['reasons'])))
        return r
    if basis == 'planned_max':
        planned = usage['planned']
        value = planned['max_planned_day_km']
        if value is None:
            return P.ResolvedInput.missing(
                'design_day_distance_km', 'km',
                note='No completed jobs with route distance estimates.')
        return P.ResolvedInput(
            name='design_day_distance_km', value=value, unit='km',
            origin=P.MODELLED, evidence_class=P.MODELLED,
            transformation=('Largest daily sum of planned route distances for '
                            f'{planned["completed_jobs"]} completed jobs in '
                            'the window. Planned, not measured.'))
    raise ValidationError({'design_day_distance': 'Unknown basis.'})


def build_readiness_snapshot(ctx):
    v, c = ctx.vehicle, ctx.candidate
    connector = ctx.body.get('charging_connector')
    if connector not in ('ac', 'dc'):
        raise ValidationError(
            {'charging_connector': 'Required: "ac" or "dc".'})

    design = _design_day(ctx)
    grid, eff, consumption = resolve_ev_grid_consumption(ctx)
    usable = S.resolved_from_record(
        'usable_battery_kwh', c.usable_battery_kwh, 'kWh', origin=c.origin,
        source=c.source, as_of=ctx.as_of, transformation='EV candidate spec')
    cand_payload = S.resolved_from_record(
        'candidate_payload_kg', c.payload_kg, 'kg', origin=c.origin,
        source=c.source, as_of=ctx.as_of, transformation='EV candidate spec')
    accept_value = c.max_ac_charge_kw if connector == 'ac' else \
        c.max_dc_charge_kw
    accept = S.resolved_from_record(
        'vehicle_max_charge_kw', accept_value, 'kW', origin=c.origin,
        source=c.source, as_of=ctx.as_of,
        transformation=f'EV candidate max {connector.upper()} charge power')

    charger_attempts = [ctx.explicit('charger_power_kw', 'charger_power_kw',
                                     subjects=('depot',))]
    if v.depot_id:
        charger_attempts.append(ctx.obs('charger_power_kw', depot=v.depot,
                                        qualifiers={'connector': connector}))
    charger = S.first_available(
        'charger_power_kw', 'kW', *(a for a in charger_attempts if a),
        missing_note=('No charger power recorded for this vehicle\'s depot '
                      f'({connector.upper()}). The depot\'s charger_count/'
                      'charging_available flags do not state power.'))

    window_attempts = [ctx.explicit('charging_window_hours',
                                    'charging_window_hours'),
                       ctx.obs('charging_window_hours', vehicle=v)]
    if v.depot_id:
        window_attempts.append(ctx.obs('charging_window_hours', depot=v.depot))
    window = S.first_available('charging_window_hours', 'h',
                               *(a for a in window_attempts if a))

    payload = S.first_available(
        'required_payload_kg', 'kg',
        *(a for a in (
            ctx.explicit('required_payload_kg', 'required_payload_kg'),
            ctx.obs('required_payload_kg', vehicle=v),
            ctx.legacy('required_payload_kg', v.payload_kg, 'kg',
                       'Like-for-like: the current vehicle\'s rated payload '
                       'from its vehicle record')) if a is not None))

    resolved = [design, consumption, eff, usable, cand_payload, accept,
                charger, window, payload, grid]
    grid.critical = False  # its components are already counted
    return {
        'inputs': _inputs(*resolved),
        'context': {
            'consumption_basis': c.consumption_basis,
            'consumption_source_type': c.consumption_source_type,
            'current_vehicle_type': v.vehicle_type,
            'candidate_vehicle_type': c.vehicle_type,
            'rated_range_km': (float(c.rated_range_km)
                               if c.rated_range_km is not None else None),
            'charging_connector': connector,
        },
    }


def _val(snapshot, name):
    entry = snapshot['inputs'].get(name)
    return None if entry is None else entry['value']


def compute_readiness(snapshot, params):
    ctx = snapshot['context']
    return readiness_calc.assess({
        'design_day_distance_km': _val(snapshot, 'design_day_distance_km'),
        'reserve_fraction': params['reserve_fraction'],
        'usable_battery_kwh': _val(snapshot, 'usable_battery_kwh'),
        'consumption_kwh_per_km': _val(snapshot,
                                       'candidate_consumption_kwh_per_km'),
        'consumption_basis': ctx['consumption_basis'],
        'consumption_source_type': ctx['consumption_source_type'],
        'charging_efficiency': _val(snapshot, 'charging_efficiency'),
        'required_payload_kg': _val(snapshot, 'required_payload_kg'),
        'candidate_payload_kg': _val(snapshot, 'candidate_payload_kg'),
        'charger_power_kw': _val(snapshot, 'charger_power_kw'),
        'vehicle_max_charge_kw': _val(snapshot, 'vehicle_max_charge_kw'),
        'charging_window_hours': _val(snapshot, 'charging_window_hours'),
        'current_vehicle_type': ctx['current_vehicle_type'],
        'candidate_vehicle_type': ctx['candidate_vehicle_type'],
        'rated_range_km': ctx['rated_range_km'],
    })


# ---------------------------------------------------------------------------
# TCO
# ---------------------------------------------------------------------------

OPTIONAL_KINDS = {
    'insurance_annual': 'insurance_cost_annual',
    'fees_annual': 'fees_annual',
    'downtime_annual': 'downtime_cost_annual',
}


def _incentives(ctx):
    out = []
    qs = S.current_observations(ctx.org).filter(
        kind='incentive_amount', ev_candidate=ctx.candidate).select_related(
        'source')
    for o in qs:
        r = S.resolved_from_observation(o, f'incentive:{o.pk}', ctx.as_of,
                                        critical=False)
        if r.evidence_class != P.VERIFIED_EXTERNAL:
            reason = ('its source has not been verified'
                      if r.evidence_class in (P.UNVERIFIED_EXTERNAL,
                                              P.ORGANIZATION_PROVIDED,
                                              P.ASSUMED, P.MODELLED,
                                              P.MEASURED)
                      else f'it is {r.stale_reason or r.evidence_class}')
            included = False
        else:
            reason, included = '', True
        out.append({'observation_id': str(o.pk),
                    'name': o.qualifiers.get('scheme'),
                    'amount': float(o.value), 'included': included,
                    'reason': reason, 'evidence': r.to_dict()})
    return out


def build_tco_snapshot(ctx, params):
    v, c = ctx.vehicle, ctx.candidate
    _require_supported_fuel(v)
    horizon = params['horizon_years']
    annual = resolve_annual_distance(ctx)
    economy = resolve_fuel_economy(ctx)
    fuel_price = _depot_then_org(ctx, 'fuel_price_per_l', 'fuel_price_per_l',
                                 qualifiers={'fuel': v.fuel_type})
    baseline = params['baseline']
    if baseline == 'retain_existing':
        ice_init = P.ResolvedInput(
            name='ice_initial_investment', value=0.0, unit=ctx.currency,
            origin=P.MODELLED, evidence_class=P.MODELLED, critical=False,
            transformation='Scenario definition: keep the existing vehicle, '
                           'so no new investment in year 0.')
    else:
        spec = ctx.explicit('ice_replacement_price', 'vehicle_purchase_price',
                            subjects=('vehicle',))
        ice_init = (spec() if spec else P.ResolvedInput.missing(
            'ice_initial_investment', ctx.currency,
            note='Baseline "replace_with_ice" needs inputs.'
                 'ice_replacement_price.'))
        if ice_init.qualifiers.get('purpose') not in (
                None, 'replacement_quote'):
            raise ValidationError({'ice_replacement_price': (
                'Use a vehicle_purchase_price observation with purpose '
                '"replacement_quote", not a historical purchase price.')})
        ice_init.name = 'ice_initial_investment'

    ice_maint = S.first_available(
        'ice_maintenance_annual', ctx.unit('maintenance_cost_annual'),
        *(a for a in (
            ctx.explicit('ice_maintenance_annual', 'maintenance_cost_annual'),
            ctx.obs('maintenance_cost_annual', vehicle=v),
            _scale(ctx.legacy('ice_maintenance_annual',
                              v.maintenance_cost_per_month,
                              ctx.unit('maintenance_cost_annual'),
                              'vehicle.maintenance_cost_per_month x 12'), 12),
        ) if a is not None))

    ev_price = S.first_available(
        'ev_purchase_price', ctx.currency,
        *(a for a in (
            ctx.explicit('ev_purchase_price', 'vehicle_purchase_price',
                         subjects=('ev_candidate',)),
            ctx.obs('vehicle_purchase_price', ev_candidate=c,
                    qualifiers={'purpose': 'candidate_quote'}),
            # A dated quotation is preferred; a manufacturer's published list
            # price (catalogue) is the fallback and is labelled as such.
            ctx.obs('vehicle_purchase_price', ev_candidate=c,
                    qualifiers={'purpose': 'published_list_price'})) if a))
    infra_attempts = [ctx.explicit('charging_infrastructure_cost',
                                   'charging_infrastructure_cost'),
                      ctx.obs('charging_infrastructure_cost', ev_candidate=c)]
    if v.depot_id:
        infra_attempts.append(ctx.obs('charging_infrastructure_cost',
                                      depot=v.depot))
    infra_attempts.append(ctx.obs('charging_infrastructure_cost',
                                  organization_level=True))
    infra = S.first_available(
        'charging_infrastructure_cost', ctx.currency,
        *(a for a in infra_attempts if a),
        missing_note='Enter the charging infrastructure cost for this '
                     'vehicle (0 if none is needed) - it is never assumed.')
    grid, eff, consumption = resolve_ev_grid_consumption(ctx)
    tariff = _depot_then_org(ctx, 'electricity_tariff_per_kwh',
                             'electricity_tariff_per_kwh')
    ev_maint = S.first_available(
        'ev_maintenance_annual', ctx.unit('maintenance_cost_annual'),
        *(a for a in (
            ctx.explicit('ev_maintenance_annual', 'maintenance_cost_annual'),
            ctx.obs('maintenance_cost_annual', ev_candidate=c)) if a),
        missing_note='EV maintenance cost must come from a quotation, service '
                     'contract or documented estimate; no saving is assumed.')

    optional = []
    for key, kind in OPTIONAL_KINDS.items():
        for side, subject in (('ice', {'vehicle': v}),
                              ('ev', {'ev_candidate': c})):
            optional.append(S.first_available(
                f'{side}_{key}', ctx.unit(kind),
                *(a for a in (ctx.explicit(f'{side}_{key}', kind),
                              ctx.obs(kind, **subject)) if a),
                critical=False))
    for side, subject in (('ice', {'vehicle': v}),
                          ('ev', {'ev_candidate': c})):
        optional.append(S.first_available(
            f'{side}_residual_value', ctx.currency,
            *(a for a in (ctx.explicit(f'{side}_residual_value',
                                       'residual_value'),
                          ctx.obs('residual_value', **subject,
                                  qualifiers={'horizon_years': horizon}))
              if a), critical=False))
    for r in optional:
        if r.present and r.name.endswith('residual_value') and \
                r.qualifiers.get('horizon_years') not in (None, horizon):
            raise ValidationError({r.name: (
                f'Residual value is for {r.qualifiers["horizon_years"]} years; '
                f'the horizon is {horizon}.')})

    consumption.critical = False
    eff.critical = False
    resolved = [annual, economy, fuel_price, ice_init, ice_maint, ev_price,
                infra, grid, consumption, eff, tariff, ev_maint, *optional]
    return {
        'inputs': _inputs(*resolved),
        'context': {'incentives': _incentives(ctx),
                    'fuel_type': v.fuel_type,
                    'currency': ctx.currency},
    }


def tco_calc_input(snapshot, params):
    i = {name: entry['value'] for name, entry in snapshot['inputs'].items()}
    i.update(horizon_years=params['horizon_years'],
             discount_rate=params['discount_rate'],
             incentives=snapshot['context']['incentives'])
    return i


def compute_tco(snapshot, params):
    i = tco_calc_input(snapshot, params)
    result = tco_calc.compare(i)
    if result['status'] == 'ok' and params.get('sensitivity_fraction'):
        result['sensitivity'] = tco_calc.sensitivity(
            i, params['sensitivity_fraction'])
    result['currency'] = snapshot['context']['currency']
    return result


# ---------------------------------------------------------------------------
# Emissions
# ---------------------------------------------------------------------------


def build_emissions_snapshot(ctx, params):
    v = ctx.vehicle
    _require_supported_fuel(v)
    annual = resolve_annual_distance(ctx)
    economy = resolve_fuel_economy(ctx)
    grid, eff, consumption = resolve_ev_grid_consumption(ctx)
    consumption.critical = False
    eff.critical = False
    fuel_factor = S.first_available(
        'fuel_factor_kgco2e_per_l', 'kgCO2e/L',
        *(a for a in (
            ctx.explicit('fuel_factor_kgco2e_per_l',
                         'fuel_emission_factor_kgco2e_per_l'),
            ctx.obs('fuel_emission_factor_kgco2e_per_l',
                    organization_level=True,
                    qualifiers={'fuel': v.fuel_type,
                                'boundary': params['fuel_boundary']}))
          if a))
    grid_attempts = [ctx.explicit('grid_factor_kgco2e_per_kwh',
                                  'grid_emission_factor_kgco2e_per_kwh')]
    q = {'boundary': params['grid_boundary']}
    if v.depot_id:
        grid_attempts.append(ctx.obs('grid_emission_factor_kgco2e_per_kwh',
                                     depot=v.depot, qualifiers=q))
    grid_attempts.append(ctx.obs('grid_emission_factor_kgco2e_per_kwh',
                                 organization_level=True, qualifiers=q))
    grid_factor = S.first_available('grid_factor_kgco2e_per_kwh',
                                    'kgCO2e/kWh',
                                    *(a for a in grid_attempts if a))
    for r, key in ((fuel_factor, 'fuel_boundary'),
                   (grid_factor, 'grid_boundary')):
        if r.present and r.qualifiers.get('boundary') not in (
                None, params[key]):
            raise ValidationError({r.name: (
                f'Factor boundary is {r.qualifiers["boundary"]}; the request '
                f'asked for {params[key]}.')})
    return {
        'inputs': _inputs(annual, economy, grid, consumption, eff,
                          fuel_factor, grid_factor),
        'context': {'fuel_type': v.fuel_type},
    }


def compute_emissions(snapshot, params):
    i = {name: e['value'] for name, e in snapshot['inputs'].items()}
    i['fuel_boundary'] = params['fuel_boundary']
    i['grid_boundary'] = params['grid_boundary']
    return emissions_calc.compare(i)


assessments.register(readiness_calc.METHOD_VERSION, compute_readiness)
assessments.register(tco_calc.METHOD_VERSION, compute_tco)
assessments.register(emissions_calc.METHOD_VERSION, compute_emissions)


# ---------------------------------------------------------------------------
# Entry points used by the views
# ---------------------------------------------------------------------------


def _number(body, key, *, low, high, integer=False):
    raw = body.get(key)
    if raw is None:
        raise ValidationError({key: 'This field is required.'})
    try:
        value = int(raw) if integer else float(raw)
    except (TypeError, ValueError):
        raise ValidationError({key: 'Must be a number.'})
    if integer and float(raw) != int(raw):
        raise ValidationError({key: 'Must be a whole number.'})
    if not low <= value <= high:
        raise ValidationError({key: f'Must be between {low} and {high}.'})
    return value


def run_readiness(*, org, user, vehicle, candidate, body):
    _require_ice(vehicle)
    as_of, tz = S.today_for(org)
    ctx = Ctx(org, vehicle, candidate, body, as_of, tz)
    params = {'reserve_fraction': _number(body, 'reserve_fraction', low=0,
                                          high=1),
              'as_of': as_of.isoformat(),
              'usage_window_days': ctx.window_days}
    snapshot = build_readiness_snapshot(ctx)
    return assessments.persist(
        org=org, user=user, kind=AssessmentKind.EV_READINESS,
        method_version=readiness_calc.METHOD_VERSION, snapshot=snapshot,
        parameters=params, vehicle=vehicle, ev_candidate=candidate,
        subject_snapshot={'vehicle': _vehicle_identity(vehicle),
                          'ev_candidate': _candidate_identity(candidate)})


def run_tco(*, org, user, vehicle, candidate, body):
    _require_ice(vehicle)
    as_of, tz = S.today_for(org)
    ctx = Ctx(org, vehicle, candidate, body, as_of, tz)
    baseline = body.get('baseline')
    if baseline not in ('retain_existing', 'replace_with_ice'):
        raise ValidationError(
            {'baseline': 'Required: "retain_existing" or "replace_with_ice".'})
    params = {
        'horizon_years': _number(body, 'horizon_years', low=1,
                                 high=tco_calc.MAX_HORIZON, integer=True),
        'discount_rate': _number(body, 'discount_rate', low=0, high=0.99),
        'discount_rate_basis': body.get('discount_rate_basis', 'real'),
        'baseline': baseline,
        'as_of': as_of.isoformat(),
        'usage_window_days': ctx.window_days,
    }
    if params['discount_rate_basis'] not in ('real', 'nominal'):
        raise ValidationError({'discount_rate_basis': 'Use real or nominal.'})
    if body.get('sensitivity_fraction') is not None:
        params['sensitivity_fraction'] = _number(
            body, 'sensitivity_fraction', low=0.01, high=0.9)
    snapshot = build_tco_snapshot(ctx, params)
    warnings = []
    if params['discount_rate_basis'] == 'nominal':
        warnings.append('Costs are held flat in today\'s money; a nominal '
                        'discount rate will understate future costs.')
    return assessments.persist(
        org=org, user=user, kind=AssessmentKind.TCO,
        method_version=tco_calc.METHOD_VERSION, snapshot=snapshot,
        parameters=params, vehicle=vehicle, ev_candidate=candidate,
        subject_snapshot={'vehicle': _vehicle_identity(vehicle),
                          'ev_candidate': _candidate_identity(candidate)},
        warnings=warnings)


def run_emissions(*, org, user, vehicle, candidate, body):
    _require_ice(vehicle)
    as_of, tz = S.today_for(org)
    ctx = Ctx(org, vehicle, candidate, body, as_of, tz)
    fb, gb = body.get('fuel_boundary'), body.get('grid_boundary')
    from evidence.kinds import FUEL_BOUNDARIES, GRID_BOUNDARIES
    if fb not in FUEL_BOUNDARIES:
        raise ValidationError({'fuel_boundary': (
            f'Required: one of {", ".join(FUEL_BOUNDARIES)}.')})
    if gb not in GRID_BOUNDARIES:
        raise ValidationError({'grid_boundary': (
            f'Required: one of {", ".join(GRID_BOUNDARIES)}.')})
    params = {'fuel_boundary': fb, 'grid_boundary': gb,
              'as_of': as_of.isoformat(),
              'usage_window_days': ctx.window_days}
    snapshot = build_emissions_snapshot(ctx, params)
    return assessments.persist(
        org=org, user=user, kind=AssessmentKind.EMISSIONS,
        method_version=emissions_calc.METHOD_VERSION, snapshot=snapshot,
        parameters=params, vehicle=vehicle, ev_candidate=candidate,
        subject_snapshot={'vehicle': _vehicle_identity(vehicle),
                          'ev_candidate': _candidate_identity(candidate)})
