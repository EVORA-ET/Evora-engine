"""Maintenance records: what the documentation requires, and what happened.

`ServiceRequirement` - an interval from a cited document (owner's manual,
service schedule, statutory inspection rule). The warning windows are the
organization's chosen lead time and are stored with the requirement, never
defaulted.

`MaintenanceEvent` - a service, inspection, repair, component replacement or
fault report as recorded by the organization. Append-only: mistakes are
voided, faults are closed by a later event that `resolves` them.

Neither model predicts anything.
"""

import uuid

from django.db import models

from evidence.models import ORIGIN_CHOICES, ImmutableModel


class ServiceRequirement(ImmutableModel):
    mutable_fields = ('retired_at', 'retired_reason')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        'organizations.Organization', on_delete=models.CASCADE,
        related_name='service_requirements')
    vehicle = models.ForeignKey(
        'vehicles.Vehicle', on_delete=models.CASCADE,
        related_name='service_requirements')
    name = models.CharField(max_length=255)
    component = models.CharField(max_length=255, blank=True, default='')
    interval_km = models.DecimalField(
        max_digits=10, decimal_places=1, null=True, blank=True)
    interval_days = models.IntegerField(null=True, blank=True)
    warning_km = models.DecimalField(
        max_digits=10, decimal_places=1, null=True, blank=True)
    warning_days = models.IntegerField(null=True, blank=True)
    source = models.ForeignKey(
        'evidence.EvidenceSource', on_delete=models.PROTECT,
        related_name='service_requirements')
    retired_at = models.DateTimeField(null=True, blank=True)
    retired_reason = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(
        'users.User', null=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'maintenance_service_requirements'
        ordering = ['name']


class EventType(models.TextChoices):
    SCHEDULED_SERVICE = 'scheduled_service'
    INSPECTION = 'inspection'
    REPAIR = 'repair'
    COMPONENT_REPLACEMENT = 'component_replacement'
    FAULT_REPORT = 'fault_report'


class FaultSeverity(models.TextChoices):
    UNKNOWN = 'unknown'
    LOW = 'low'
    MEDIUM = 'medium'
    HIGH = 'high'
    CRITICAL = 'critical'


class MaintenanceEvent(ImmutableModel):
    mutable_fields = ('voided_at', 'void_reason', 'voided_by')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        'organizations.Organization', on_delete=models.CASCADE,
        related_name='maintenance_events')
    vehicle = models.ForeignKey(
        'vehicles.Vehicle', on_delete=models.CASCADE,
        related_name='maintenance_events')
    event_type = models.CharField(max_length=30, choices=EventType.choices)
    occurred_on = models.DateField()
    completed_on = models.DateField(null=True, blank=True)
    odometer_km = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True)
    requirement = models.ForeignKey(
        ServiceRequirement, null=True, blank=True, on_delete=models.PROTECT,
        related_name='events',
        help_text='The service requirement this event satisfies.')
    component = models.CharField(max_length=255, blank=True, default='')
    fault_code = models.CharField(max_length=64, blank=True, default='')
    fault_severity = models.CharField(
        max_length=20, choices=FaultSeverity.choices, blank=True, default='')
    resolves = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.PROTECT,
        related_name='resolutions',
        help_text='The fault report this event closes.')
    description = models.TextField(blank=True, default='')
    provider = models.CharField(max_length=255, blank=True, default='')
    cost = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=10, blank=True, default='')
    downtime_hours = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True)
    origin = models.CharField(max_length=32, choices=ORIGIN_CHOICES)
    source = models.ForeignKey(
        'evidence.EvidenceSource', null=True, blank=True,
        on_delete=models.PROTECT, related_name='maintenance_events')

    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.TextField(blank=True, default='')
    voided_by = models.ForeignKey(
        'users.User', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='+')
    created_by = models.ForeignKey(
        'users.User', null=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'maintenance_events'
        ordering = ['-occurred_on', '-created_at']
        indexes = [models.Index(fields=['vehicle', 'occurred_on'])]
