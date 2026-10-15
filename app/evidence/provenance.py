"""Provenance vocabulary and evidence-coverage rules (plain Python).

Origin vs. evidence class
-------------------------
Every stored value records its **origin** - how the number came to exist:

measured               read from an instrument or a transactional record
                       (odometer, fuel receipt, utility meter, BMS export,
                       workshop job card). Requires a cited source.
external               published by a third party (manufacturer, regulator,
                       utility, government notification, vendor quotation).
                       Requires a cited source.
organization_provided  stated by the organization without a cited record
                       (a planning figure, a manager's estimate, the legacy
                       vehicle fields).
modelled               calculated by EVORA or another model from other values;
                       the transformation is recorded.
assumed                a value chosen for analysis with no evidence behind it.

The **evidence class** shown to users is derived from the origin plus the
source's verification and freshness:

measured            origin measured, not stale
verified_external   origin external AND a named user has verified the source
                    against the cited document, not stale
unverified_external origin external, source not (yet) verified
organization_provided / modelled / assumed   as stored
stale               older than the kind's freshness limit, outside its
                    effective period, or its source failed its last refresh
missing             no usable value

A value typed by a user is never "verified" by being typed; a manufacturer
figure is "external", not "measured".

Evidence coverage (per assessment)
----------------------------------
Each calculation names its *critical* inputs. Decision parameters (discount
rate, horizon, reserve fraction) are choices, not evidence, and are excluded.

insufficient  any critical input missing or invalid, or no critical input
              exists at all (no result is produced)
low           all critical inputs present, but at least one is assumed or stale
moderate      all critical inputs present and fresh, none assumed, and at
              least one is organization_provided / unverified_external /
              modelled
high          every critical input is measured or verified_external and fresh

Coverage describes the evidence behind a result. It is NOT a probability that
the result is correct, and it is never blended with readiness, cost or risk.
"""

from dataclasses import asdict, dataclass, field

MEASURED = 'measured'
EXTERNAL = 'external'
ORGANIZATION_PROVIDED = 'organization_provided'
MODELLED = 'modelled'
ASSUMED = 'assumed'
ORIGINS = (MEASURED, EXTERNAL, ORGANIZATION_PROVIDED, MODELLED, ASSUMED)
ORIGINS_REQUIRING_SOURCE = (MEASURED, EXTERNAL)
INLINE_ORIGINS = (ORGANIZATION_PROVIDED, ASSUMED)

VERIFIED_EXTERNAL = 'verified_external'
UNVERIFIED_EXTERNAL = 'unverified_external'
STALE = 'stale'
MISSING = 'missing'

STRONG_CLASSES = (MEASURED, VERIFIED_EXTERNAL)
MIDDLE_CLASSES = (ORGANIZATION_PROVIDED, UNVERIFIED_EXTERNAL, MODELLED)

HIGH = 'high'
MODERATE = 'moderate'
LOW = 'low'
INSUFFICIENT = 'insufficient'
COVERAGE_LEVELS = (HIGH, MODERATE, LOW, INSUFFICIENT)


def evidence_class(origin, source_verified, stale):
    if stale:
        return STALE
    if origin == EXTERNAL:
        return VERIFIED_EXTERNAL if source_verified else UNVERIFIED_EXTERNAL
    return origin


@dataclass
class ResolvedInput:
    """One input to a calculation, with everything needed to audit it."""

    name: str
    value: float | None
    unit: str
    origin: str | None = None
    evidence_class: str = MISSING
    stale: bool = False
    stale_reason: str = ''
    observation_id: str | None = None
    source: dict | None = None
    observed_at: str | None = None
    period: dict | None = None
    effective: dict | None = None
    qualifiers: dict = field(default_factory=dict)
    transformation: str = ''
    note: str = ''
    critical: bool = True
    # ids of the observations/records a derived value was calculated from
    derived_from_ids: list = field(default_factory=list)

    @property
    def present(self):
        return self.value is not None

    def to_dict(self):
        return asdict(self)

    @classmethod
    def missing(cls, name, unit, note='', critical=True):
        return cls(name=name, value=None, unit=unit, note=note,
                   critical=critical)

    @classmethod
    def from_dict(cls, data):
        return cls(**data)


def coverage(inputs):
    """Classify evidence coverage for a set of ResolvedInputs.

    Returns (level, reasons) where reasons explain the classification.
    """
    critical = [i for i in inputs if i.critical]
    if not critical:
        return INSUFFICIENT, ['No evidence-bearing inputs were available.']
    missing = [i.name for i in critical if not i.present]
    if missing:
        return INSUFFICIENT, [f'Missing critical input: {n}.' for n in missing]

    reasons = []
    weak = [i for i in critical if i.evidence_class in (ASSUMED, STALE)]
    if weak:
        for i in weak:
            reasons.append(f'{i.name} is {i.evidence_class.replace("_", " ")}'
                           + (f' ({i.stale_reason})' if i.stale_reason else '')
                           + '.')
        return LOW, reasons

    middle = [i for i in critical if i.evidence_class in MIDDLE_CLASSES]
    if middle:
        for i in middle:
            reasons.append(
                f'{i.name} is {i.evidence_class.replace("_", " ")}.')
        return MODERATE, reasons

    return HIGH, ['Every critical input is measured or verified external '
                  'evidence and within its freshness limit.']
