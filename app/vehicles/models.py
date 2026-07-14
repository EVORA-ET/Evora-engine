import uuid

from django.contrib.gis.db import models


class VehicleType(models.TextChoices):
    BUS = 'bus'
    TRUCK = 'truck'
    CAR = 'car'
    VAN = 'van'
    TRAILER = 'trailer'
    OTHER = 'other'


class FuelType(models.TextChoices):
    DIESEL = 'diesel'
    PETROL = 'petrol'
    EV = 'ev'
    CNG = 'cng'
    LPG = 'lpg'
    OTHER = 'other'


class VehicleStatus(models.TextChoices):
    ACTIVE = 'active'
    INACTIVE = 'inactive'
    UNDER_MAINTENANCE = 'under_maintenance'
    RETIRED = 'retired'
    SOLD = 'sold'


class UploadStatus(models.TextChoices):
    PENDING = 'pending'
    PROCESSING = 'processing'
    COMPLETED = 'completed'
    FAILED = 'failed'


class Vehicle(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        'organizations.Organization',
        on_delete=models.CASCADE,
        related_name='vehicles',
    )
    registration_number = models.CharField(max_length=100)
    vin = models.CharField(max_length=100, blank=True, default='', null=True)
    chassis_number = models.CharField(max_length=100, blank=True, default='', null=True)
    manufacturer = models.CharField(max_length=255)
    model = models.CharField(max_length=255)
    variant = models.CharField(max_length=255, blank=True, default='', null=True)
    manufacture_year = models.IntegerField()
    purchase_date = models.DateField()
    vehicle_type = models.CharField(
        max_length=50, choices=VehicleType.choices
    )
    fuel_type = models.CharField(
        max_length=50, choices=FuelType.choices
    )
    engine_cc = models.IntegerField(null=True, blank=True)
    fuel_tank_capacity = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True
    )
    battery_capacity_kwh = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True
    )
    mileage_kmpl = models.DecimalField(max_digits=6, decimal_places=2)
    payload_kg = models.DecimalField(max_digits=10, decimal_places=2)
    seating_capacity = models.IntegerField()
    gvw_kg = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(
        max_length=50, choices=VehicleStatus.choices,
        default=VehicleStatus.ACTIVE,
    )
    current_location = models.PointField(
        srid=4326, geography=True, null=True, blank=True
    )
    assigned_driver_id = models.UUIDField(null=True, blank=True)
    odometer_km = models.DecimalField(max_digits=12, decimal_places=2)
    avg_daily_km = models.DecimalField(max_digits=8, decimal_places=2)
    avg_monthly_km = models.DecimalField(max_digits=10, decimal_places=2)
    trips_per_day = models.DecimalField(max_digits=6, decimal_places=2)
    fuel_cost_per_month = models.DecimalField(max_digits=12, decimal_places=2)
    maintenance_cost_per_month = models.DecimalField(
        max_digits=12, decimal_places=2
    )
    insurance_expiry = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'vehicles'


class BulkUpload(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        'organizations.Organization',
        on_delete=models.CASCADE,
        related_name='bulk_uploads',
    )
    file = models.FileField(upload_to='bulk_uploads/')
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
        db_table = 'bulk_uploads'
