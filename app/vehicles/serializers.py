from rest_framework import serializers
from rest_framework_gis.fields import GeometryField

from vehicles.models import Vehicle, BulkUpload


class VehicleSerializer(serializers.ModelSerializer):
    current_location = GeometryField(required=False, allow_null=True)
    organization_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = Vehicle
        fields = [
            'id',
            'organization_id',
            'registration_number',
            'vin',
            'chassis_number',
            'manufacturer',
            'model',
            'variant',
            'manufacture_year',
            'purchase_date',
            'vehicle_type',
            'fuel_type',
            'engine_cc',
            'fuel_tank_capacity',
            'battery_capacity_kwh',
            'mileage_kmpl',
            'payload_kg',
            'seating_capacity',
            'gvw_kg',
            'status',
            'current_location',
            'assigned_driver_id',
            'odometer_km',
            'avg_daily_km',
            'avg_monthly_km',
            'trips_per_day',
            'fuel_cost_per_month',
            'maintenance_cost_per_month',
            'insurance_expiry',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id', 'organization_id', 'created_at', 'updated_at',
        ]


class VehicleUpdateSerializer(serializers.ModelSerializer):
    current_location = GeometryField(required=False, allow_null=True)

    class Meta:
        model = Vehicle
        fields = [
            'registration_number',
            'vin',
            'chassis_number',
            'manufacturer',
            'model',
            'variant',
            'manufacture_year',
            'purchase_date',
            'vehicle_type',
            'fuel_type',
            'engine_cc',
            'fuel_tank_capacity',
            'battery_capacity_kwh',
            'mileage_kmpl',
            'payload_kg',
            'seating_capacity',
            'gvw_kg',
            'status',
            'current_location',
            'assigned_driver_id',
            'odometer_km',
            'avg_daily_km',
            'avg_monthly_km',
            'trips_per_day',
            'fuel_cost_per_month',
            'maintenance_cost_per_month',
            'insurance_expiry',
        ]


class BulkUploadSerializer(serializers.ModelSerializer):
    class Meta:
        model = BulkUpload
        fields = [
            'id',
            'file',
            'status',
            'total_rows',
            'processed_rows',
            'failed_rows',
            'error_report',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id', 'status', 'total_rows', 'processed_rows',
            'failed_rows', 'error_report', 'created_at', 'updated_at',
        ]


class BulkUploadStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = BulkUpload
        fields = [
            'id',
            'status',
            'total_rows',
            'processed_rows',
            'failed_rows',
            'error_report',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields
