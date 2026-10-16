from datetime import datetime, timedelta, timezone as dt_tz
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase

from evidence import provenance as P
from evidence.usage import (
    EnergyEntry,
    Reading,
    fill_to_fill,
    nearest_rank_percentile,
    odometer_profile,
)

UTC = dt_tz.utc
IST = ZoneInfo('Asia/Kolkata')
T0 = datetime(2026, 1, 1, 18, 0, tzinfo=UTC)  # 23:30 IST


def daily(distances, start=T0):
    """End-of-day readings with the given daily distances."""
    out, km = [Reading(start, 10000.0, 'r0')], 10000.0
    for i, d in enumerate(distances, start=1):
        km += d
        out.append(Reading(start + timedelta(days=i), km, f'r{i}'))
    return out


class OdometerProfileTests(SimpleTestCase):
    window = (T0 - timedelta(days=1), T0 + timedelta(days=400))

    def test_annualised_and_daily_statistics(self):
        dists = [100.0] * 20 + [0.0] * 5 + [200.0] * 10  # 35 days
        p = odometer_profile(daily(dists), *self.window, IST)
        self.assertEqual(p['status'], 'ok')
        self.assertEqual(p['distance_km'], sum(dists))  # 4000
        self.assertEqual(p['span_days'], 35.0)
        self.assertAlmostEqual(p['annualised_distance_km'],
                               round(4000 / 35 * 365, 2))
        d = p['daily']
        self.assertEqual(d['sample_count'], 35)
        self.assertEqual(d['max_observed_km'], 200.0)
        self.assertEqual(d['operating_days'], 30)
        self.assertAlmostEqual(d['mean_km'], round(4000 / 35, 2))
        self.assertEqual(d['median_km'], 100.0)
        # nearest rank: ceil(0.9*35)=32nd smallest -> 200
        self.assertEqual(d['p90_km'], 200.0)

    def test_missing_days_are_not_zero_distance_days(self):
        readings = daily([100.0] * 10)
        # drop days 4..6: the gap produces no samples at all
        kept = [r for r in readings if r.id not in ('r4', 'r5', 'r6')]
        p = odometer_profile(kept, *self.window, IST)
        self.assertEqual(p['daily']['operating_days'],
                         p['daily']['sample_count'])
        self.assertEqual(p['daily']['sample_count'], 6)
        self.assertEqual(p['distance_km'], 1000.0)  # total still counted

    def test_thin_data_gives_nulls_with_reasons(self):
        p = odometer_profile(daily([50.0] * 5), *self.window, IST)
        self.assertIsNone(p['annualised_distance_km'])
        self.assertIsNone(p['daily']['mean_km'])
        self.assertIsNone(p['daily']['p90_km'])
        self.assertEqual(p['daily']['max_observed_km'], 50.0)
        self.assertTrue(p['reasons'])

    def test_decreasing_odometer_invalidates(self):
        readings = daily([10.0, 10.0])
        readings.append(Reading(T0 + timedelta(days=3), 5.0, 'bad'))
        p = odometer_profile(readings, *self.window, IST)
        self.assertEqual(p['status'], 'invalid')
        self.assertIn('bad', p['invalid_reading_ids'])
        self.assertIsNone(p['distance_km'])

    def test_single_reading_insufficient(self):
        p = odometer_profile([Reading(T0, 1.0)], *self.window, IST)
        self.assertEqual(p['status'], 'insufficient')

    def test_percentile_nearest_rank(self):
        self.assertEqual(nearest_rank_percentile(list(range(1, 11)), 90), 9)
        self.assertEqual(nearest_rank_percentile([5.0], 90), 5.0)


class FillToFillTests(SimpleTestCase):
    def test_reference_fuel_economy(self):
        e = [
            EnergyEntry(T0, 40.0, True, 10000.0, id='a'),       # anchor
            EnergyEntry(T0 + timedelta(days=3), 20.0, False, None, id='b'),
            EnergyEntry(T0 + timedelta(days=6), 30.0, True, 10500.0, id='c'),
        ]
        r = fill_to_fill(e, kind='fuel')
        # fuel after the first full fill: 20 + 30 = 50 L for 500 km
        self.assertEqual(r['status'], 'ok')
        self.assertEqual(r['fuel_l'], 50.0)
        self.assertEqual(r['km_per_l'], 10.0)
        self.assertEqual(r['record_ids'], ['b', 'c'])

    def test_needs_two_full_anchors_and_distance(self):
        e = [EnergyEntry(T0, 40.0, True, 10000.0)]
        self.assertEqual(fill_to_fill(e, kind='fuel')['status'],
                         'insufficient')
        e.append(EnergyEntry(T0 + timedelta(days=1), 5.0, True, 10050.0))
        self.assertEqual(fill_to_fill(e, kind='fuel')['status'],
                         'insufficient')

    def test_mixed_charging_bases_rejected(self):
        e = [
            EnergyEntry(T0, 50.0, True, 1000.0, 'grid'),
            EnergyEntry(T0 + timedelta(days=1), 40.0, False, None, 'vehicle'),
            EnergyEntry(T0 + timedelta(days=2), 60.0, True, 1400.0, 'grid'),
        ]
        self.assertEqual(fill_to_fill(e, kind='charging')['status'],
                         'invalid')

    def test_charging_kwh_per_km(self):
        e = [
            EnergyEntry(T0, 50.0, True, 1000.0, 'grid'),
            EnergyEntry(T0 + timedelta(days=2), 100.0, True, 1400.0, 'grid'),
        ]
        r = fill_to_fill(e, kind='charging')
        self.assertEqual(r['kwh_per_km'], 0.25)
        self.assertEqual(r['basis'], 'grid')


def ri(name, cls, present=True, critical=True, stale=False):
    return P.ResolvedInput(name=name, value=1.0 if present else None,
                           unit='x', evidence_class=cls, stale=stale,
                           critical=critical)


class CoverageTests(SimpleTestCase):
    def test_levels(self):
        self.assertEqual(P.coverage([ri('a', P.MEASURED),
                                     ri('b', P.VERIFIED_EXTERNAL)])[0],
                         P.HIGH)
        self.assertEqual(P.coverage([ri('a', P.MEASURED),
                                     ri('b', P.ORGANIZATION_PROVIDED)])[0],
                         P.MODERATE)
        self.assertEqual(P.coverage([ri('a', P.MEASURED),
                                     ri('b', P.UNVERIFIED_EXTERNAL)])[0],
                         P.MODERATE)
        self.assertEqual(P.coverage([ri('a', P.MEASURED),
                                     ri('b', P.ASSUMED)])[0], P.LOW)
        self.assertEqual(P.coverage([ri('a', P.MEASURED),
                                     ri('b', P.STALE, stale=True)])[0], P.LOW)
        self.assertEqual(P.coverage([ri('a', P.MEASURED),
                                     ri('b', P.MISSING, present=False)])[0],
                         P.INSUFFICIENT)

    def test_non_critical_inputs_do_not_lower_coverage(self):
        self.assertEqual(P.coverage([ri('a', P.MEASURED),
                                     ri('b', P.ASSUMED, critical=False)])[0],
                         P.HIGH)

    def test_no_critical_inputs_is_insufficient(self):
        self.assertEqual(P.coverage([])[0], P.INSUFFICIENT)

    def test_evidence_class(self):
        self.assertEqual(P.evidence_class(P.EXTERNAL, False, False),
                         P.UNVERIFIED_EXTERNAL)
        self.assertEqual(P.evidence_class(P.EXTERNAL, True, False),
                         P.VERIFIED_EXTERNAL)
        self.assertEqual(P.evidence_class(P.EXTERNAL, True, True), P.STALE)
        self.assertEqual(P.evidence_class(P.ORGANIZATION_PROVIDED, True,
                                          False), P.ORGANIZATION_PROVIDED)
