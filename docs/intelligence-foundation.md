# EVORA shared intelligence foundation (Phase 0–1)

The backend foundation for **EV Migration** and **Predictive Health &
Maintenance**. It reuses the Operations records (vehicles, depots, jobs) and
adds no second fleet database. Every number an assessment uses carries its
origin, source, dates and evidence class. Every assessment is stored
immutably with the method version that produced it.

## 1. Apps and responsibilities

| App | Owns | Plain-Python modules (no Django) |
|---|---|---|
| `evidence` (shared) | `EvidenceSource`, `Observation`, `EnergyRecord`, `Assessment`; write validation; freshness; input resolution; usage profile; assessment persistence/reproduction; external refresh contract | `kinds.py`, `provenance.py`, `usage.py`, `errors.py` |
| `ev_migration` | `EVCandidate`, `MigrationDecision`; resolution of migration inputs | `calc/readiness.py`, `calc/tco.py`, `calc/emissions.py` |
| `maintenance` | `ServiceRequirement`, `MaintenanceEvent`; rule evaluation | `rules.py` |

The flow is: request → `services` resolve each input (with provenance) →
snapshot → registered `compute(snapshot, parameters)` → coverage
classification → immutable `Assessment`. Views only translate HTTP.

Migrations are additive. The first set creates the new tables. The
follow-up set (`evidence/0002`, `ev_migration/0003`, `maintenance/0002`)
changes delete rules, which Django enforces in Python, so no SQL runs for
those. It also creates one table, `evidence_source_verifications`. No
existing table or column is changed. All three apps roll back cleanly to
zero.

## 2. Provenance

**Origin** (stored): `measured`, `external`, `organization_provided`,
`modelled` or `assumed`. Measured and external values must cite an
`EvidenceSource`. Modelled values must state their transformation.

**Evidence class** (derived when read):

| Class | Meaning |
|---|---|
| measured | Origin measured, within freshness limit |
| verified_external | Origin external, and a named user verified the source against the cited document |
| unverified_external | Origin external, source not verified |
| organization_provided / modelled / assumed | As stored |
| stale | Past `effective_to`, not yet in force, older than the kind's `max_age_days`, or its source failed (or is overdue for) refresh |
| missing | No usable value |

Derived values (e.g. grid-side kWh/km from vehicle-side kWh/km ÷ charging
efficiency) have origin `modelled`. They inherit the **weakest** class of
their components.

Evidence is append-only:

- **Observations** are corrected with `/supersede`. The original stays and
  remains referenced by old assessments.
- **Energy and maintenance records** are voided, not deleted.
- **Service requirements** are retired.
- **Assessments, candidates and decisions** are immutable. The model `save()`
  refuses edits.
- **Duplicates are refused.** A second current observation with the same
  kind, subject, qualifiers and time key is rejected (correct it with
  `/supersede` instead). A second non-voided fuel or charging record for the
  same vehicle, type and exact time is also rejected. These are
  application-level checks; two simultaneous identical requests could still
  both succeed.
- **Deletion.** Every link from evidence to a vehicle, depot, EV candidate or
  source uses `on_delete=RESTRICT`. Deleting one of those records on its own
  while it has evidence, maintenance history or assessments returns
  **409** (`evidence.exceptions.exception_handler`, registered as DRF's
  `EXCEPTION_HANDLER`). Deleting a whole organization still cascades.
  Records without evidence delete exactly as before.
- **Verification history.** Every verify/reject decision, and every reset
  caused by a refresh, is appended to `SourceVerification` and returned as
  `verification_history` on the source.
- **Derived inputs.** When a measured input is derived from stored records
  (annualised distance, design-day distance, fill-to-fill economy), the ids
  of every record used are stored in that input's `derived_from_ids`.

## 3. Observation kinds (`evidence/kinds.py`, `GET /api/evidence/kinds`)

Each kind fixes:

- a canonical unit (other units are **rejected, never converted**; currency
  is the organization's own);
- the subjects it can describe;
- a plausible range;
- its temporal type (instant, period or effective);
- a freshness limit;
- its required qualifiers.

SoC (`battery_soc_pct`) and SoH (`battery_soh_pct`, with a required
`method`) are separate kinds. Measured and BMS-estimated capacity are
distinguished by the `method` qualifier.

The freshness limits (e.g. fuel price 90 days, tariff 400 days, grid factor
730 days, odometer 30 days) are **policy choices made in this phase**. They
are surfaced in the API and should be reviewed by the operator.

## 4. Calculations (all units explicit; all tested)

### Usage (`usage/1.0.0`)

Built only from dated odometer readings and fuel/charging records.

- **Annualised distance** = distance ÷ span_days × 365. Requires a span of
  at least 28 days.
- **Daily distance** = last reading of day D − last reading of day D−1.
  Days without readings give **no sample**; they are never treated as zero.
- Mean, median and coefficient of variation need at least 7 samples.
- P90 (nearest rank) needs at least 30 samples.
- Fuel and EV consumption use the fill-to-fill method between two *full*
  records carrying odometer readings, at least 300 km apart.

Planned distance (route estimates of COMPLETED jobs) and the legacy vehicle
fields are reported separately and labelled. They are never presented as
measured.

### Readiness gates (`ev-readiness/1.0.0`)

```
required_range = design_day_km × (1 + reserve)
battery_energy = required_range × c_battery            ≤ usable_battery_kWh
grid_energy    = design_day_km × c_grid
charging_hours = grid_energy / min(charger_kW, vehicle_max_kW) ≤ window_h
payload        : candidate_payload_kg ≥ required_payload_kg
```

Charging efficiency is applied **once**:

- vehicle-basis consumption: `c_grid = c / η`;
- grid-basis consumption: `c_battery = c × η`.

If η is unknown for a grid-basis figure, the range gate uses `c` (which is
conservative) and says so. Certified range is shown, never used.

Any **fail** gives `not_suitable`. Any **insufficient** gives
`insufficient_evidence`. Only `meets_requirements` is
`eligible_for_scoring`. A migration decision of `approve_transition` is
refused without a readiness assessment that meets requirements.

No weighted score exists yet.

### TCO (`tco/1.0.0`)

```
NPC = I0 + Σ_{t=1..N} C/(1+r)^t − R/(1+r)^N
ICE energy = km / km_per_L × price_per_L
EV energy  = km × kWh_grid_per_km × tariff
EV I0      = price + charging infrastructure − verified incentives in force
```

Outputs:

- net present savings (NPC_ice − NPC_ev);
- levelised cost per km (NPC ÷ discounted km);
- operating cost per km;
- simple payback (undiscounted, only when meaningful);
- discounted payback (interpolated; null if not reached within N);
- one-at-a-time ±x% sensitivity.

Rules:

- Optional categories (insurance, fees, downtime, residual) are included
  only when **both** scenarios have them.
- Unverified incentives are listed and excluded.
- Charging infrastructure cost must be entered explicitly (0 is allowed).
- Missing mandatory inputs return `insufficient` with the list. They are
  never zero.
- Costs are flat in today's money, so a real discount rate is the
  consistent choice. A nominal rate is accepted with a warning.

### Emissions (`emissions/1.0.0`)

```
ICE: km / km_per_L × factor_kgCO2e_per_L
EV:  km × kWh_grid_per_km × grid_factor
```

The fuel boundary (`tank_to_wheel` or `well_to_wheel`) and grid boundary
(`generation` or `consumption_incl_td_losses`) are mandatory request
parameters. They must match the stored factor's qualifier.

The result names the comparison type (`operational`, `well_to_wheel` or
`mixed`) and what it favours. The EV figure is generation emissions; only
tailpipe is zero. Lifecycle (manufacturing and end-of-life) is excluded.

### Maintenance (`maintenance-rules/1.0.0`)

Schedule checks only:

- **Time interval**: due date = last satisfying event + interval_days. With
  no recorded event, it counts from the purchase date, and this is flagged.
- **Distance interval**: due odometer = odometer at last event +
  interval_km. With no baseline odometer, the result is undetermined.
- Each requirement keeps its own warning windows. None are defaulted.
- **Faults** stay open until a later event `resolves` them.
- **Battery**: latest SoH vs the warranty threshold (when both exist), and
  capacity retention vs nominal (labelled measured or estimated). SoC is
  shown separately and never used for health.
- Record totals count missing costs and downtime instead of zeroing them.
- `vehicle.status = under_maintenance` is explicitly *not* treated as a
  fault.
- `predictive_assessment.available` is always `false`.

## 5. Evidence coverage

Coverage is calculated over each assessment's *critical* inputs. Decision
parameters are excluded.

| Level | Rule |
|---|---|
| insufficient | Any critical input missing, or no critical input at all |
| low | Any critical input assumed or stale |
| moderate | All fresh, none assumed, at least one organization_provided / unverified_external / modelled |
| high | All critical inputs measured or verified_external and fresh |

Coverage describes the evidence. It is **not** a probability. It is never
merged with readiness, cost or maintenance status.

## 6. External data

No provider is integrated. I found no official machine-readable API for the
first-needed data (Indian retail fuel prices, state electricity tariff
orders, the CEA CO₂ baseline grid factor, OEM specs and quotations). These
enter through cited manual entry or `POST /api/evidence/observations/import`.
The import is atomic: one bad row rejects the whole file. Values then need
`POST /api/evidence/sources/{id}/verify`.

`evidence/refresh.py` defines the adapter contract for future APIs:

- fetched values are stored as `external` and **unverified**;
- verification resets on each refresh;
- a failed or partially invalid refresh stores nothing and marks the source
  `failed`, so earlier values from it read as **stale**.

`ADAPTERS` is empty.

| Data | Retrieval now | Applicability qualifiers | Refresh |
|---|---|---|---|
| Fuel price | Manual/CSV from a dated notice | `fuel`, depot or org | 90-day staleness |
| Electricity tariff | Utility bill / tariff order | depot or org, `tariff_category` | 400 days |
| Grid emission factor | Published database edition | `boundary`, `grid_region` | 730 days |
| Fuel emission factor | Cited methodology | `fuel`, `boundary` | 5 years |
| EV specs | `EVCandidate` + source | vehicle_type | per source |
| Quotes, residuals, incentives | Candidate observations | `purpose`, `horizon_years`, `scheme` | 180 days (quotes) |
| Service intervals | `ServiceRequirement` + manual | per vehicle | per source |

## 7. Data contracts for future predictive models (not built)

The tables above are the training substrate. No model exists, and none
should be built until the following is in place.

**Failure / repair risk**

- *Target*: `MaintenanceEvent` repairs and fault reports per component,
  with time-to-event from usage.
- *Features*: odometer-derived usage, service adherence, fault history.
- *Requirements*: enough resolved outcomes per component; a time-based
  split with no future leakage; a baseline (interval schedule); calibration
  measured before any probability is shown.

**Energy consumption**

- *Target*: fill-to-fill kWh/km or km/L.
- *Features*: route, payload and season. These need new captured fields;
  they do not exist yet.

**Battery degradation**

- *Target*: SoH or capacity-test series per vehicle (`method` recorded).
- *Requirements*: repeated measurements over time. Age-only estimation is
  disallowed.

## 8. Endpoints

```
GET        /api/evidence/kinds
GET|POST   /api/evidence/sources            GET /api/evidence/sources/{id}
POST       /api/evidence/sources/{id}/verify
GET|POST   /api/evidence/observations       GET /api/evidence/observations/{id}
POST       /api/evidence/observations/{id}/supersede
POST       /api/evidence/observations/import          (multipart CSV, atomic)
GET|POST   /api/evidence/energy-records     POST /api/evidence/energy-records/{id}/void
GET        /api/evidence/vehicles/{id}/usage?window_days=
GET        /api/evidence/assessments        GET /api/evidence/assessments/{id}
POST       /api/evidence/assessments/{id}/reproduce
GET|POST   /api/ev-migration/candidates     GET /api/ev-migration/candidates/{id}
POST       /api/ev-migration/assessments/readiness|tco|emissions
GET|POST   /api/ev-migration/decisions
GET|POST   /api/maintenance/requirements    POST /api/maintenance/requirements/{id}/retire
GET|POST   /api/maintenance/events          POST /api/maintenance/events/{id}/void
GET        /api/maintenance/vehicles/{id}/status?window_days=
POST       /api/maintenance/assessments
```

Lists follow the existing convention: a bare array by default, or a page
envelope with `page`/`page_size`. Typed client:
`frontend/src/lib/intelligenceApi.ts`.
