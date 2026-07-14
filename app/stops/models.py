import uuid

from django.contrib.gis.db import models


class StopType(models.TextChoices):
    CUSTOMER = 'customer'
    WAREHOUSE = 'warehouse'
    CHARGING_STATION = 'charging_station'
    HUB = 'hub'


class UploadStatus(models.TextChoices):
    PENDING = 'pending'
    PROCESSING = 'processing'
    COMPLETED = 'completed'
    FAILED = 'failed'


class Stop(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        'organizations.Organization',
        on_delete=models.CASCADE,
        related_name='stops',
    )
    name = models.CharField(max_length=255)
    address = models.TextField()
    location = models.PointField(srid=4326, geography=True, null=True, blank=True)
    stop_type = models.CharField(
        max_length=50, choices=StopType.choices
    )
    service_time_minutes = models.IntegerField(default=0)
    parking_capacity = models.IntegerField(default=0)
    workshop_available = models.BooleanField(default=False)
    fuel_station_available = models.BooleanField(default=False)
    charging_available = models.BooleanField(default=False)
    charger_count = models.IntegerField(default=0)
    maintenance_bays = models.IntegerField(default=0)
    operating_hours = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'stops'


class BulkUpload(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        'organizations.Organization',
        on_delete=models.CASCADE,
        related_name='stop_bulk_uploads',
    )
    file = models.FileField(upload_to='bulk_uploads/stops/')
    status = models.CharField(
        max_length=50, choices=UploadStatus.choices,
        default=UploadStatus.PENDING,
    )
    total_rows = models.IntegerField(default=0)
    processed_rows = models.IntegerField(default=0)
    failed_rows = models.IntegerField(default=0)
    error_report = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'stop_bulk_uploads'
