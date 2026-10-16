"""Registry of observation kinds.

An observation kind fixes, once, what a stored number means:

* its canonical unit (no silent conversion: a value arriving in any other unit
  is rejected, so "12 km/L" can never be stored as if it were "12 L/100 km");
* which subject it describes (a vehicle, a depot, an EV candidate model or the
  organization as a whole);
* the physically/commercially plausible range;
* how time applies to it (an instant reading, a value over a period, or a
  value effective between two dates);
* how old it may be before it is reported as stale;
* which qualifiers are required to interpret it (e.g. a fuel emission factor
  is meaningless without the fuel and the emissions boundary).

``{currency}`` in a unit is replaced by the organization's currency, so an
INR organization stores tariffs in ``INR/kWh``.

This module is plain Python (no Django) so the calculation layer and tests can
import it freely.
"""

from dataclasses import dataclass, field

VEHICLE = 'vehicle'
DEPOT = 'depot'
EV_CANDIDATE = 'ev_candidate'
ORGANIZATION = 'organization'
SUBJECTS = (VEHICLE, DEPOT, EV_CANDIDATE, ORGANIZATION)

INSTANT = 'instant'      # a reading taken at observed_at
PERIOD = 'period'        # a value describing period_start..period_end
EFFECTIVE = 'effective'  # a rate/price/factor in force effective_from..to


@dataclass(frozen=True)
class Kind:
    key: str
    label: str
    unit: str
    subjects: tuple
    temporal: str
    min_value: float
    max_value: float
    min_inclusive: bool = True
    max_age_days: int | None = None
    # qualifier name -> tuple of allowed values (None = free text)
    qualifiers: dict = field(default_factory=dict)
    required_qualifiers: tuple = ()
    description: str = ''

    def unit_for(self, currency):
        return self.unit.replace('{currency}', currency or '')

    def to_dict(self, currency=None):
        return {
            'key': self.key,
            'label': self.label,
            'unit': self.unit_for(currency) if currency else self.unit,
            'subjects': list(self.subjects),
            'temporal': self.temporal,
            'min_value': self.min_value,
            'min_inclusive': self.min_inclusive,
            'max_value': self.max_value,
            'max_age_days': self.max_age_days,
            'qualifiers': {
                k: (list(v) if v is not None else None)
                for k, v in self.qualifiers.items()
            },
            'required_qualifiers': list(self.required_qualifiers),
            'description': self.description,
        }


FUELS = ('diesel', 'petrol')
FUEL_BOUNDARIES = ('tank_to_wheel', 'well_to_wheel')
GRID_BOUNDARIES = ('generation', 'consumption_incl_td_losses')

_KINDS = [
    # ---- vehicle usage ------------------------------------------------
    Kind(
        'odometer_km', 'Odometer reading', 'km', (VEHICLE,), INSTANT,
        0, 5_000_000, max_age_days=30,
        description='Odometer as read at observed_at. Usage statistics are '
        'derived only from these dated readings.',
    ),
    Kind(
        'annual_distance_km', 'Annual distance (planning figure)', 'km/year',
        (VEHICLE,), EFFECTIVE, 0, 500_000, min_inclusive=False,
        max_age_days=400,
        description='Distance the organization states the vehicle covers per '
        'year. Used only when measured odometer history is insufficient.',
    ),
    Kind(
        'fuel_economy_km_per_l', 'Fuel economy', 'km/L', (VEHICLE,), PERIOD,
        0, 100, min_inclusive=False, max_age_days=400,
        description='Distance per litre over a stated period. Prefer fuel '
        'records (fill-to-fill), which EVORA derives itself.',
    ),
    Kind(
        'required_payload_kg', 'Required payload', 'kg', (VEHICLE,),
        EFFECTIVE, 0, 100_000,
        description='The heaviest load this vehicle\'s duty must carry.',
    ),
    # ---- battery (kept as separate kinds: SoC is not SoH) ---------------
    Kind(
        'battery_soc_pct', 'Battery state of charge (SoC)', '%', (VEHICLE,),
        INSTANT, 0, 100,
        description='How full the battery is at an instant. Says nothing about '
        'battery health and is never used as a health input.',
    ),
    Kind(
        'battery_soh_pct', 'Battery state of health (SoH)', '%', (VEHICLE,),
        INSTANT, 0, 110, min_inclusive=False, max_age_days=180,
        qualifiers={'method': ('bms_reported', 'capacity_test',
                               'service_report')},
        required_qualifiers=('method',),
        description='Remaining capacity relative to new, as reported by the '
        'stated method.',
    ),
    Kind(
        'battery_capacity_kwh', 'Battery capacity', 'kWh', (VEHICLE,),
        INSTANT, 0, 2000, min_inclusive=False, max_age_days=365,
        qualifiers={'method': ('capacity_test', 'bms_estimate')},
        required_qualifiers=('method',),
        description='Usable capacity. "capacity_test" is measured; '
        '"bms_estimate" is the battery management system\'s estimate.',
    ),
    Kind(
        'battery_temperature_c', 'Battery temperature', 'degC', (VEHICLE,),
        INSTANT, -50, 100,
    ),
    Kind(
        'battery_warranty_soh_threshold_pct',
        'Battery warranty SoH threshold', '%', (VEHICLE, EV_CANDIDATE),
        EFFECTIVE, 0, 100, min_inclusive=False,
        description='SoH below which the battery warranty applies, from the '
        'warranty document.',
    ),
    # ---- costs ----------------------------------------------------------
    Kind(
        'vehicle_purchase_price', 'Vehicle purchase price', '{currency}',
        (VEHICLE, EV_CANDIDATE), EFFECTIVE, 0, 1_000_000_000,
        min_inclusive=False, max_age_days=180,
        qualifiers={'purpose': ('replacement_quote', 'candidate_quote',
                                'historical_purchase',
                                'published_list_price')},
        required_qualifiers=('purpose',),
        description='A dated quotation or invoice price.',
    ),
    Kind(
        'maintenance_cost_annual', 'Annual maintenance cost',
        '{currency}/year', (VEHICLE, EV_CANDIDATE), EFFECTIVE,
        0, 1_000_000_000, max_age_days=400,
    ),
    Kind(
        'insurance_cost_annual', 'Annual insurance cost', '{currency}/year',
        (VEHICLE, EV_CANDIDATE), EFFECTIVE, 0, 1_000_000_000,
        max_age_days=400,
    ),
    Kind(
        'fees_annual', 'Annual taxes and fees', '{currency}/year',
        (VEHICLE, EV_CANDIDATE), EFFECTIVE, 0, 1_000_000_000,
        max_age_days=400,
    ),
    Kind(
        'downtime_cost_annual', 'Annual downtime cost', '{currency}/year',
        (VEHICLE, EV_CANDIDATE), EFFECTIVE, 0, 1_000_000_000,
        max_age_days=400,
    ),
    Kind(
        'residual_value', 'Residual value at end of horizon', '{currency}',
        (VEHICLE, EV_CANDIDATE), EFFECTIVE, 0, 1_000_000_000,
        qualifiers={'horizon_years': None},
        required_qualifiers=('horizon_years',),
        description='Expected resale value after `horizon_years` years.',
    ),
    Kind(
        'incentive_amount', 'Purchase incentive', '{currency}',
        (EV_CANDIDATE,), EFFECTIVE, 0, 1_000_000_000, min_inclusive=False,
        qualifiers={'scheme': None},
        required_qualifiers=('scheme',),
        description='Only included in financial results when its source is '
        'verified and the incentive is in force.',
    ),
    Kind(
        'charging_infrastructure_cost', 'Charging infrastructure cost',
        '{currency}', (DEPOT, EV_CANDIDATE, ORGANIZATION), EFFECTIVE,
        0, 1_000_000_000, max_age_days=180,
        description='One-off cost attributable to electrifying one vehicle. '
        'Zero is allowed but must be entered explicitly.',
    ),
    # ---- energy prices and factors ----------------------------------------
    Kind(
        'fuel_price_per_l', 'Fuel price', '{currency}/L', (ORGANIZATION, DEPOT),
        EFFECTIVE, 0, 10_000, min_inclusive=False, max_age_days=90,
        qualifiers={'fuel': FUELS}, required_qualifiers=('fuel',),
    ),
    Kind(
        'electricity_tariff_per_kwh', 'Electricity tariff (energy charge)',
        '{currency}/kWh', (ORGANIZATION, DEPOT), EFFECTIVE, 0, 1000,
        min_inclusive=False, max_age_days=400,
        qualifiers={'tariff_category': None},
        description='All-in energy charge per kWh drawn from the grid. Fixed '
        'and demand charges are not included.',
    ),
    Kind(
        'fuel_emission_factor_kgco2e_per_l', 'Fuel emission factor',
        'kgCO2e/L', (ORGANIZATION,), EFFECTIVE, 0, 10, min_inclusive=False,
        max_age_days=1825,
        qualifiers={'fuel': FUELS, 'boundary': FUEL_BOUNDARIES},
        required_qualifiers=('fuel', 'boundary'),
    ),
    Kind(
        'grid_emission_factor_kgco2e_per_kwh', 'Grid emission factor',
        'kgCO2e/kWh', (ORGANIZATION, DEPOT), EFFECTIVE, 0, 3,
        max_age_days=730,
        qualifiers={'boundary': GRID_BOUNDARIES, 'grid_region': None},
        required_qualifiers=('boundary',),
    ),
    # ---- charging ---------------------------------------------------------
    Kind(
        'charger_power_kw', 'Charger power available to one vehicle', 'kW',
        (DEPOT,), EFFECTIVE, 0, 1000, min_inclusive=False,
        qualifiers={'connector': ('ac', 'dc')},
        required_qualifiers=('connector',),
        description='Power one vehicle can actually draw, after any sharing or '
        'load management at the depot.',
    ),
    Kind(
        'charging_window_hours', 'Daily charging window', 'h',
        (DEPOT, VEHICLE), EFFECTIVE, 0, 24, min_inclusive=False,
        description='Hours per day the vehicle is parked at a charger.',
    ),
    Kind(
        'charging_efficiency_fraction', 'Charging efficiency (grid to battery)',
        'fraction', (EV_CANDIDATE, DEPOT, ORGANIZATION), EFFECTIVE,
        0, 1, min_inclusive=False,
    ),
]

KINDS = {k.key: k for k in _KINDS}


def get_kind(key):
    return KINDS.get(key)


def in_range(kind, value):
    low_ok = value >= kind.min_value if kind.min_inclusive else (
        value > kind.min_value)
    return low_ok and value <= kind.max_value


def range_text(kind):
    low = '>=' if kind.min_inclusive else '>'
    return f'{low} {kind.min_value:g} and <= {kind.max_value:g}'
