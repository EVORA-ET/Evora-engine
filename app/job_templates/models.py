import uuid

from django.contrib.gis.db import models


class JobTemplateStatus(models.TextChoices):
    ACTIVE = 'ACTIVE'
    PAUSED = 'PAUSED'
    ARCHIVED = 'ARCHIVED'


class JobTemplate(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        'organizations.Organization',
        on_delete=models.CASCADE,
        related_name='job_templates',
    )
    source_depot = models.ForeignKey(
        'depots.Depot',
        on_delete=models.CASCADE,
        related_name='job_templates',
    )
    destination_depot = models.ForeignKey(
        'depots.Depot',
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='destination_job_templates',
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default='')
    is_recurring = models.BooleanField(default=False)
    recurrence_rule = models.TextField(blank=True, default='')
    is_permanent = models.BooleanField(default=False)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    estimated_duration_minutes = models.IntegerField(null=True, blank=True)
    estimated_distance_km = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    estimated_energy_kwh = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=JobTemplateStatus.choices, default=JobTemplateStatus.ACTIVE,
    )
    created_by = models.ForeignKey(
        'users.User',
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_job_templates',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'job_templates'
