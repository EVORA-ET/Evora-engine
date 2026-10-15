"""Vehicle usage features from measured records (plain Python, no Django).

Definitions (method ``usage/1.0.0``)
------------------------------------
Readings      dated odometer observations inside the requested window.
              A reading lower than an earlier one makes the series invalid;
              nothing is computed until it is corrected.
Distance      last reading - first reading in the window.
Annualised    distance / span_days * 365, where span_days is the time
distance      between the first and last reading. Only produced when
              span_days >= 28. Seasonality is not corrected for.
Daily         one sample per local calendar day D that has a reading AND
distance      whose previous calendar day D-1 also has a reading:
              last(D) - last(D-1). Days without readings produce no sample;
              they are never treated as zero-distance days.
Mean/median/  require >= 7 daily samples. Coefficient of variation is the
variability   population standard deviation / mean.
P90           requires >= 30 daily samples; nearest-rank method
              (the ceil(0.9 * n)-th smallest sample).
Max observed  the largest daily sample, always reported with its sample count.
Operating     sampled days with a daily distance > 0 (only sampled days count).
days
Fill-to-fill  between the first and the last *full* record that carries an
consumption   odometer reading: quantity = every record after the first full
              record up to and including the last; distance = odometer
              difference. Requires >= 300 km between the two full records.
"""

import math
import statistics
from dataclasses import dataclass
from datetime import timedelta

from evidence.errors import CalculationError

METHOD_VERSION = 'usage/1.0.0'
MIN_SPAN_DAYS_FOR_ANNUAL = 28
MIN_SAMPLES_CENTRAL = 7
MIN_SAMPLES_P90 = 30
MIN_FILL_TO_FILL_DISTANCE_KM = 300.0


@dataclass(frozen=True)
class Reading:
    at: object  # aware datetime
    km: float
    id: str = ''


@dataclass(frozen=True)
class EnergyEntry:
    at: object
    quantity: float
    is_full: bool
    odometer_km: float | None = None
    basis: str = ''
    id: str = ''


def nearest_rank_percentile(samples, pct):
    if not samples:
        raise CalculationError('samples', 'No samples.')
    ordered = sorted(samples)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


def _r(value, places=2):
    return None if value is None else round(value, places)


def odometer_profile(readings, window_start, window_end, tz):
    """Usage statistics from odometer readings. Never raises for thin data:
    returns explicit nulls with the reason instead."""
    window_days = (window_end - window_start).total_seconds() / 86400
    inside = sorted(
        (r for r in readings if window_start <= r.at <= window_end),
        key=lambda r: (r.at, r.km),
    )
    out = {
        'method_version': METHOD_VERSION,
        'window': {
            'start': window_start.isoformat(),
            'end': window_end.isoformat(),
            'days': _r(window_days, 1),
            'timezone': str(tz),
        },
        'reading_count': len(inside),
        'status': 'ok',
        'reasons': [],
        'first_reading': None,
        'last_reading': None,
        'distance_km': None,
        'span_days': None,
        'window_coverage_fraction': None,
        'annualised_distance_km': None,
        'daily': {
            'sample_count': 0,
            'mean_km': None,
            'median_km': None,
            'p90_km': None,
            'max_observed_km': None,
            'coefficient_of_variation': None,
            'operating_days': None,
            'operating_day_share': None,
        },
    }

    for prev, cur in zip(inside, inside[1:]):
        if cur.km < prev.km:
            out['status'] = 'invalid'
            out['reasons'].append(
                f'Odometer decreases from {prev.km:g} km ({prev.at.isoformat()}) '
                f'to {cur.km:g} km ({cur.at.isoformat()}). Correct or supersede '
                'the wrong reading before usage can be calculated.')
            out['invalid_reading_ids'] = [prev.id, cur.id]
            return out

    if len(inside) < 2:
        out['status'] = 'insufficient'
        out['reasons'].append(
            'At least two odometer readings inside the window are needed.')
        return out

    first, last = inside[0], inside[-1]
    out['reading_ids'] = [r.id for r in inside]
    distance = last.km - first.km
    span = (last.at - first.at).total_seconds() / 86400
    out['first_reading'] = {'at': first.at.isoformat(), 'km': first.km}
    out['last_reading'] = {'at': last.at.isoformat(), 'km': last.km}
    out['distance_km'] = _r(distance)
    out['span_days'] = _r(span, 2)
    out['window_coverage_fraction'] = _r(
        min(1.0, span / window_days) if window_days > 0 else None, 3)
    if span >= MIN_SPAN_DAYS_FOR_ANNUAL:
        out['annualised_distance_km'] = _r(distance / span * 365)
    else:
        out['reasons'].append(
            f'Readings span {span:.1f} days; annualised distance needs at '
            f'least {MIN_SPAN_DAYS_FOR_ANNUAL}.')

    last_per_day = {}
    for r in inside:
        day = r.at.astimezone(tz).date()
        last_per_day[day] = r  # sorted ascending, so the last one wins
    samples = []
    for day, reading in last_per_day.items():
        prev = last_per_day.get(day - timedelta(days=1))
        if prev is not None:
            samples.append(reading.km - prev.km)

    daily = out['daily']
    daily['sample_count'] = len(samples)
    if samples:
        daily['max_observed_km'] = _r(max(samples))
        daily['operating_days'] = sum(1 for s in samples if s > 0)
        daily['operating_day_share'] = _r(
            daily['operating_days'] / len(samples), 3)
    if len(samples) >= MIN_SAMPLES_CENTRAL:
        mean = statistics.fmean(samples)
        daily['mean_km'] = _r(mean)
        daily['median_km'] = _r(statistics.median(samples))
        if mean > 0:
            daily['coefficient_of_variation'] = _r(
                statistics.pstdev(samples) / mean, 3)
    else:
        out['reasons'].append(
            f'{len(samples)} daily samples; mean/median/variability need '
            f'{MIN_SAMPLES_CENTRAL}.')
    if len(samples) >= MIN_SAMPLES_P90:
        daily['p90_km'] = _r(nearest_rank_percentile(samples, 90))
    else:
        out['reasons'].append(
            f'{len(samples)} daily samples; the 90th percentile needs '
            f'{MIN_SAMPLES_P90}.')
    return out


def fill_to_fill(entries, *, kind):
    """Consumption between full records. `kind` is 'fuel' or 'charging'."""
    ordered = sorted(entries, key=lambda e: e.at)
    with_odo = [e for e in ordered if e.odometer_km is not None]
    for prev, cur in zip(with_odo, with_odo[1:]):
        if cur.odometer_km < prev.odometer_km:
            return {
                'status': 'invalid',
                'reasons': [
                    f'Odometer on record {cur.id} ({cur.odometer_km:g} km) is '
                    f'lower than on earlier record {prev.id} '
                    f'({prev.odometer_km:g} km).'],
            }
    anchors = [e for e in with_odo if e.is_full]
    if len(anchors) < 2:
        return {
            'status': 'insufficient',
            'reasons': ['Needs two records marked full that both carry an '
                        'odometer reading.'],
        }
    first, last = anchors[0], anchors[-1]
    distance = last.odometer_km - first.odometer_km
    if distance < MIN_FILL_TO_FILL_DISTANCE_KM:
        return {
            'status': 'insufficient',
            'reasons': [f'Only {distance:g} km between full records; at least '
                        f'{MIN_FILL_TO_FILL_DISTANCE_KM:g} km is required.'],
        }
    used = [e for e in ordered if first.at < e.at <= last.at]
    quantity = sum(e.quantity for e in used)
    bases = sorted({e.basis for e in used})
    if kind == 'charging' and len(bases) != 1:
        return {
            'status': 'invalid',
            'reasons': ['Charging records mix grid-metered and vehicle-reported '
                        'energy; they cannot be summed.'],
        }
    if quantity <= 0:
        return {'status': 'insufficient',
                'reasons': ['No energy recorded between full records.']}
    result = {
        'status': 'ok',
        'reasons': [],
        'method_version': METHOD_VERSION,
        'from': {'at': first.at.isoformat(), 'odometer_km': first.odometer_km,
                 'record_id': first.id},
        'to': {'at': last.at.isoformat(), 'odometer_km': last.odometer_km,
               'record_id': last.id},
        'distance_km': _r(distance),
        'records_used': len(used),
        'record_ids': [e.id for e in used],
    }
    if kind == 'fuel':
        result['fuel_l'] = _r(quantity, 3)
        result['km_per_l'] = _r(distance / quantity, 3)
        result['l_per_100km'] = _r(quantity / distance * 100, 3)
    else:
        result['energy_kwh'] = _r(quantity, 3)
        result['basis'] = bases[0]
        result['kwh_per_km'] = _r(quantity / distance, 4)
    return result
