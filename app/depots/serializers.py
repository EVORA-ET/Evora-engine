from rest_framework import serializers
from rest_framework_gis.fields import GeometryField

from depots.models import BulkUpload, Depot


def validate_operating_hours(value):
    if value is not None and not isinstance(value, dict):
        raise serializers.ValidationError(
            'operating_hours must be a JSON object (dictionary)'
        )
    return value


class DepotSerializer(serializers.ModelSerializer):
    location = GeometryField(required=False, allow_null=True)
    organization_id = serializers.UUIDField(read_only=True)
    parking_capacity = serializers.IntegerField(
        required=False, min_value=0, default=0
    )
    charger_count = serializers.IntegerField(
        required=False, min_value=0, default=0
    )
    maintenance_bays = serializers.IntegerField(
        required=False, min_value=0, default=0
    )

    class Meta:
        model = Depot
        fields = [
            'id',
            'organization_id',
            'name',
            'address',
            'location',
            'parking_capacity',
            'workshop_available',
            'fuel_station_available',
            'charging_available',
            'charger_count',
            'maintenance_bays',
            'operating_hours',
            'depot_manager_name',
            'depot_manager_contact',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id', 'organization_id', 'created_at', 'updated_at',
        ]

    def validate_operating_hours(self, value):
        return validate_operating_hours(value)


class DepotUpdateSerializer(serializers.ModelSerializer):
    location = GeometryField(required=False, allow_null=True)
    parking_capacity = serializers.IntegerField(
        required=False, min_value=0,
    )
    charger_count = serializers.IntegerField(
        required=False, min_value=0,
    )
    maintenance_bays = serializers.IntegerField(
        required=False, min_value=0,
    )

    class Meta:
        model = Depot
        fields = [
            'name',
            'address',
            'location',
            'parking_capacity',
            'workshop_available',
            'fuel_station_available',
            'charging_available',
            'charger_count',
            'maintenance_bays',
            'operating_hours',
            'depot_manager_name',
            'depot_manager_contact',
        ]

    def validate_operating_hours(self, value):
        return validate_operating_hours(value)


class BulkUploadSerializer(serializers.ModelSerializer):
    class Meta:
        model = BulkUpload
        ref_name = 'DepotBulkUpload'
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
        ref_name = 'DepotBulkUploadStatus'
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
