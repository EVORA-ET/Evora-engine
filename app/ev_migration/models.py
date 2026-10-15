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
