import uuid

from django.contrib.gis.db import models
from django.contrib.postgres.fields import ArrayField


class DriverStatus(models.TextChoices):
    ACTIVE = 'active'
    INACTIVE = 'inactive'
    ON_LEAVE = 'on_leave'
    SUSPENDED = 'suspended'
    TERMINATED = 'terminated'


class UploadStatus(models.TextChoices):
    PENDING = 'pending'
    PROCESSING = 'processing'
    COMPLETED = 'completed'
    FAILED = 'failed'


class Driver(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        'organizations.Organization',
        on_delete=models.CASCADE,
        related_name='drivers',
    )
    user = models.ForeignKey(
        'users.User',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='driver_profile',
    )
    driver_code = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=255)
    date_of_birth = models.DateField()
    license_number = models.CharField(max_length=100, unique=True)
    license_class = models.CharField(max_length=50)
    license_expiry = models.DateField()
    experience_years = models.DecimalField(max_digits=4, decimal_places=1)
    job_id = models.UUIDField(null=True, blank=True)
    status = models.CharField(
        max_length=50, choices=DriverStatus.choices,
        default=DriverStatus.ACTIVE,
    )
    shift = models.CharField(max_length=100, blank=True, default='')
    emergency_contact_name = models.CharField(
        max_length=255, blank=True, default=''
    )
    emergency_contact_phone = models.CharField(
        max_length=20, blank=True, default=''
    )
    languages = ArrayField(
        models.CharField(max_length=100), blank=True, default=list
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'drivers'


class BulkUpload(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        'organizations.Organization',
        on_delete=models.CASCADE,
        related_name='driver_bulk_uploads',
    )
    file = models.FileField(upload_to='bulk_uploads/drivers/')
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
        db_table = 'driver_bulk_uploads'
