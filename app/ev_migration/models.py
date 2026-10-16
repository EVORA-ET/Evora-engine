"""EV Migration domain records.

`EVCandidate` is a specification of an EV model the organization is
considering, always tied to a cited source. Prices, incentives and residual
values for a candidate are dated `evidence.Observation`s (subject
`ev_candidate`) so they can change without rewriting the specification.

`MigrationDecision` records what the organization decided and which immutable
assessments informed it.
"""

import uuid

from django.db import models

from evidence.models import ORIGIN_CHOICES, ImmutableModel
from vehicles.models import VehicleType


class ConsumptionBasis(models.TextChoices):
    GRID = 'grid'
    VEHICLE = 'vehicle'


class ConsumptionSourceType(models.TextChoices):
    MANUFACTURER_RATED = 'manufacturer_rated'
    MEASURED_TRIAL = 'measured_trial'
    MEASURED_FLEET = 'measured_fleet'
    # Battery capacity / published range. Set only by the catalogue for
    # models whose manufacturer does not publish kWh/km; origin "modelled".
    DERIVED_FROM_RATED_RANGE = 'derived_from_rated_range'


class RangeBasis(models.TextChoices):
    CERTIFIED = 'certified'
    MANUFACTURER_CLAIM = 'manufacturer_claim'


class CatalogueVerification(models.TextChoices):
    UNVERIFIED = 'unverified'
    VERIFIED = 'verified'
    REJECTED = 'rejected'


class EVModel(models.Model):
    """One EV model in EVORA's shared catalogue (not organization data).

    Every figure is copied from the cited manufacturer document exactly as
    published; anything the document does not state stays null. Entries are
    maintained by EVORA staff (Django admin or management commands), never
    by customers. Organizations assess a model through an `EVCandidate`
    that EVORA creates from the entry, so assessments keep an immutable
    snapshot even if the catalogue is corrected later.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    manufacturer = models.CharField(max_length=255)
    model = models.CharField(max_length=255)
    variant = models.CharField(max_length=255, blank=True, default='')
    vehicle_type = models.CharField(
        max_length=50, choices=VehicleType.choices)
    body = models.CharField(max_length=255, blank=True, default='')

    battery_capacity_kwh = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True)
    battery_capacity_is_usable = models.BooleanField(
        default=False, help_text='True only when the document states the '
        'figure is usable (net) capacity.')
    energy_consumption_kwh_per_km = models.DecimalField(
        max_digits=8, decimal_places=4, null=True, blank=True,
        help_text='Only when the manufacturer publishes it.')
    published_range_km = models.DecimalField(
        max_digits=8, decimal_places=1, null=True, blank=True)
    range_basis = models.CharField(
        max_length=30, choices=RangeBasis.choices, blank=True, default='')
    range_cycle = models.CharField(max_length=100, blank=True, default='')
    payload_kg = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True)
    gvw_kg = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True)
    max_ac_charge_kw = models.DecimalField(
        max_digits=7, decimal_places=2, null=True, blank=True)
    max_dc_charge_kw = models.DecimalField(
        max_digits=7, decimal_places=2, null=True, blank=True)
    charging_notes = models.TextField(blank=True, default='')

    list_price = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True)
    price_currency = models.CharField(max_length=10, blank=True, default='')
    price_basis = models.CharField(max_length=255, blank=True, default='',
                                   help_text='e.g. "ex-showroom, pan-India"')
    price_as_of = models.DateField(null=True, blank=True)

    source_title = models.CharField(max_length=255)
    source_publisher = models.CharField(max_length=255)
    source_url = models.URLField(max_length=1000)
    source_published_on = models.DateField(null=True, blank=True)
    retrieved_at = models.DateTimeField()
    notes = models.TextField(blank=True, default='',
                             help_text='Discrepancies, conditions, caveats.')

    verification_status = models.CharField(
        max_length=20, choices=CatalogueVerification.choices,
        default=CatalogueVerification.UNVERIFIED)
    verified_by_name = models.CharField(max_length=255, blank=True,
                                        default='')
    verified_at = models.DateTimeField(null=True, blank=True)
    verification_note = models.TextField(blank=True, default='')

    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'ev_catalogue_models'
        ordering = ['manufacturer', 'model', 'variant']
        constraints = [models.UniqueConstraint(
            fields=['manufacturer', 'model', 'variant'],
            name='uniq_catalogue_model')]

    def label(self):
        return ' '.join(
            p for p in (self.manufacturer, self.model, self.variant) if p)

    def consumption(self):
        """(kWh/km, source type) or (None, None) when it cannot be stated."""
        if self.energy_consumption_kwh_per_km is not None:
            return (self.energy_consumption_kwh_per_km,
                    ConsumptionSourceType.MANUFACTURER_RATED)
        if self.battery_capacity_kwh and self.published_range_km:
            return (round(self.battery_capacity_kwh
                          / self.published_range_km, 4),
                    ConsumptionSourceType.DERIVED_FROM_RATED_RANGE)
        return None, None

    def missing_specs(self):
        """Specifications an assessment needs that the document lacks."""
        missing = []
        if self.battery_capacity_kwh is None:
            missing.append('battery_capacity_kwh')
        if self.consumption()[0] is None:
            missing.append('energy_consumption_kwh_per_km')
        if self.payload_kg is None:
            missing.append('payload_kg')
        return missing


class EVCandidate(ImmutableModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        'organizations.Organization', on_delete=models.CASCADE,
        related_name='ev_candidates')
    manufacturer = models.CharField(max_length=255)
    model = models.CharField(max_length=255)
    variant = models.CharField(max_length=255, blank=True, default='')
    vehicle_type = models.CharField(
        max_length=50, choices=VehicleType.choices)

    usable_battery_kwh = models.DecimalField(max_digits=8, decimal_places=2)
    energy_consumption_kwh_per_km = models.DecimalField(
        max_digits=8, decimal_places=4)
    consumption_basis = models.CharField(
        max_length=10, choices=ConsumptionBasis.choices)
    consumption_source_type = models.CharField(
        max_length=30, choices=ConsumptionSourceType.choices)
    consumption_conditions = models.TextField(
        blank=True, default='',
        help_text='Test cycle, load, route type behind the consumption figure.')
    payload_kg = models.DecimalField(max_digits=10, decimal_places=2)
    gvw_kg = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True)
    max_ac_charge_kw = models.DecimalField(
        max_digits=7, decimal_places=2, null=True, blank=True)
    max_dc_charge_kw = models.DecimalField(
        max_digits=7, decimal_places=2, null=True, blank=True)
    rated_range_km = models.DecimalField(
        max_digits=8, decimal_places=1, null=True, blank=True,
        help_text='Manufacturer-certified range. Displayed for reference; '
        'never used as real-world range.')
    rated_range_cycle = models.CharField(max_length=100, blank=True, default='')

    origin = models.CharField(max_length=32, choices=ORIGIN_CHOICES)
    source = models.ForeignKey(
        'evidence.EvidenceSource', on_delete=models.RESTRICT,
        related_name='ev_candidates')
    catalogue_model = models.ForeignKey(
        EVModel, null=True, blank=True, on_delete=models.RESTRICT,
        related_name='candidates',
        help_text='Set when EVORA created this candidate from its catalogue.')
    created_by = models.ForeignKey(
        'users.User', null=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'ev_candidates'
        ordering = ['manufacturer', 'model', 'variant']

    def label(self):
        return ' '.join(
            p for p in (self.manufacturer, self.model, self.variant) if p)


class DecisionType(models.TextChoices):
    APPROVE_TRANSITION = 'approve_transition'
    DEFER = 'defer'
    REJECT = 'reject'


class MigrationDecision(ImmutableModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        'organizations.Organization', on_delete=models.CASCADE,
        related_name='migration_decisions')
    vehicle = models.ForeignKey(
        'vehicles.Vehicle', null=True, on_delete=models.RESTRICT,
        related_name='migration_decisions')
    vehicle_snapshot = models.JSONField(default=dict)
    ev_candidate = models.ForeignKey(
        EVCandidate, null=True, blank=True, on_delete=models.RESTRICT,
        related_name='decisions')
    decision = models.CharField(max_length=30, choices=DecisionType.choices)
    readiness_assessment = models.ForeignKey(
        'evidence.Assessment', null=True, blank=True,
        on_delete=models.RESTRICT, related_name='+')
    tco_assessment = models.ForeignKey(
        'evidence.Assessment', null=True, blank=True,
        on_delete=models.RESTRICT, related_name='+')
    emissions_assessment = models.ForeignKey(
        'evidence.Assessment', null=True, blank=True,
        on_delete=models.RESTRICT, related_name='+')
    rationale = models.TextField()
    decided_by = models.ForeignKey(
        'users.User', null=True, on_delete=models.SET_NULL, related_name='+')
    decided_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'migration_decisions'
        ordering = ['-decided_at']


# ---------------------------------------------------------------------------
# Migration planning
# ---------------------------------------------------------------------------
#
# Plans are working documents (editable), unlike assessments and decisions.
# Item status only ever changes through an explicit status change, which is
# recorded in PlanItemStatusChange. Nothing is marked in progress or completed
# automatically, and an approved decision does not complete anything.


class PlanStatus(models.TextChoices):
    DRAFT = 'draft'
    ACTIVE = 'active'
    ARCHIVED = 'archived'


class MigrationPlan(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        'organizations.Organization', on_delete=models.CASCADE,
        related_name='migration_plans')
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default='')
    status = models.CharField(
        max_length=20, choices=PlanStatus.choices, default=PlanStatus.DRAFT)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    budget_amount = models.DecimalField(
        max_digits=16, decimal_places=2, null=True, blank=True,
        help_text='Total budget the organization has set for this plan.')
    currency = models.CharField(max_length=10)
    assumptions = models.TextField(
        blank=True, default='',
        help_text='Planning assumptions, stated by the organization.')
    target_ev_share_pct = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text='Share of the whole fleet the organization aims to have '
        'electric by end_date. A goal, not progress.')
    created_by = models.ForeignKey(
        'users.User', null=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'migration_plans'
        ordering = ['-created_at']


class PlanItemStatus(models.TextChoices):
    PLANNED = 'planned'
    IN_PROGRESS = 'in_progress'
    COMPLETED = 'completed'
    DEFERRED = 'deferred'
    CANCELLED = 'cancelled'


PLAN_ITEM_TRANSITIONS = {
    PlanItemStatus.PLANNED: {PlanItemStatus.IN_PROGRESS,
                             PlanItemStatus.DEFERRED,
                             PlanItemStatus.CANCELLED},
    PlanItemStatus.DEFERRED: {PlanItemStatus.PLANNED,
                              PlanItemStatus.CANCELLED},
    PlanItemStatus.IN_PROGRESS: {PlanItemStatus.COMPLETED,
                                 PlanItemStatus.DEFERRED,
                                 PlanItemStatus.CANCELLED},
    PlanItemStatus.CANCELLED: {PlanItemStatus.PLANNED},
    PlanItemStatus.COMPLETED: set(),
}


class MigrationPlanItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    plan = models.ForeignKey(
        MigrationPlan, on_delete=models.CASCADE, related_name='items')
    vehicle = models.ForeignKey(
        'vehicles.Vehicle', on_delete=models.RESTRICT,
        related_name='migration_plan_items')
    ev_candidate = models.ForeignKey(
        EVCandidate, null=True, blank=True, on_delete=models.RESTRICT,
        related_name='plan_items')
    phase = models.PositiveSmallIntegerField()
    target_date = models.DateField(null=True, blank=True)
    estimated_cost = models.DecimalField(
        max_digits=16, decimal_places=2, null=True, blank=True,
        help_text='Budget line for this transition; null means not estimated.')
    readiness_assessment = models.ForeignKey(
        'evidence.Assessment', null=True, blank=True,
        on_delete=models.RESTRICT, related_name='+')
    tco_assessment = models.ForeignKey(
        'evidence.Assessment', null=True, blank=True,
        on_delete=models.RESTRICT, related_name='+')
    decision = models.ForeignKey(
        MigrationDecision, null=True, blank=True, on_delete=models.RESTRICT,
        related_name='plan_items')
    status = models.CharField(
        max_length=20, choices=PlanItemStatus.choices,
        default=PlanItemStatus.PLANNED)
    completed_on = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(
        'users.User', null=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'migration_plan_items'
        ordering = ['phase', 'target_date', 'created_at']
        constraints = [
            models.UniqueConstraint(fields=['plan', 'vehicle'],
                                    name='uniq_plan_vehicle'),
        ]


class PlanItemStatusChange(models.Model):
    """Append-only history of plan item status changes."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    item = models.ForeignKey(
        MigrationPlanItem, on_delete=models.CASCADE,
        related_name='status_history')
    from_status = models.CharField(max_length=20, blank=True, default='')
    to_status = models.CharField(max_length=20)
    note = models.TextField(blank=True, default='')
    changed_by = models.ForeignKey(
        'users.User', null=True, on_delete=models.SET_NULL, related_name='+')
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'migration_plan_item_status_changes'
        ordering = ['changed_at']
