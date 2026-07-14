import uuid

from django.contrib.gis.db import models


class InstanceStopStatus(models.TextChoices):
    PENDING = 'PENDING'
    ARRIVED = 'ARRIVED'
    SKIPPED = 'SKIPPED'
    COMPLETED = 'COMPLETED'


class InstanceStop(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job_instance = models.ForeignKey(
        'job_instances.JobInstance',
        on_delete=models.CASCADE,
        related_name='instance_stops',
    )
    stop = models.ForeignKey(
        'stops.Stop',
        on_delete=models.CASCADE,
        related_name='instance_stops',
    )
    sequence_number = models.IntegerField()
    estimated_arrival = models.DateTimeField(null=True, blank=True)
    actual_arrival = models.DateTimeField(null=True, blank=True)
    actual_departure = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=InstanceStopStatus.choices,
        default=InstanceStopStatus.PENDING,
    )
    delay_minutes = models.IntegerField(default=0)
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'instance_stops'
        ordering = ['sequence_number']
