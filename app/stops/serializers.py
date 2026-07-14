from rest_framework import serializers
from rest_framework_gis.fields import GeometryField

from stops.models import BulkUpload, Stop


def validate_operating_hours(value):
    if value is not None and not isinstance(value, dict):
        raise serializers.ValidationError(
            'operating_hours must be a JSON object (dictionary)'
        )
    return value


class StopSerializer(serializers.ModelSerializer):
    location = GeometryField(required=False, allow_null=True)
    organization_id = serializers.UUIDField(read_only=True)
    parking_capacity = serializers.IntegerField(
        required=False, min_value=0, default=0
    )
    service_time_minutes = serializers.IntegerField(
        required=False, min_value=0, default=0
    )
    charger_count = serializers.IntegerField(
        required=False, min_value=0, default=0
    )
    maintenance_bays = serializers.IntegerField(
        required=False, min_value=0, default=0
    )

    class Meta:
        model = Stop
        fields = [
            'id',
            'organization_id',
            'name',
            'address',
            'location',
            'stop_type',
            'service_time_minutes',
            'parking_capacity',
            'workshop_available',
            'fuel_station_available',
            'charging_available',
            'charger_count',
            'maintenance_bays',
            'operating_hours',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id', 'organization_id', 'created_at', 'updated_at',
        ]

    def validate_location(self, value):
        if value is not None:
            from django.contrib.gis.geos import Point
            if not isinstance(value, Point):
                raise serializers.ValidationError(
                    'location must be a GeoJSON Point geometry'
                )
        return value

    def validate_operating_hours(self, value):
        return validate_operating_hours(value)


class StopUpdateSerializer(serializers.ModelSerializer):
    location = GeometryField(required=False, allow_null=True)
    parking_capacity = serializers.IntegerField(
        required=False, min_value=0,
    )
    service_time_minutes = serializers.IntegerField(
        required=False, min_value=0,
    )
    charger_count = serializers.IntegerField(
        required=False, min_value=0,
    )
    maintenance_bays = serializers.IntegerField(
        required=False, min_value=0,
    )

    class Meta:
        model = Stop
        fields = [
            'name',
            'address',
            'location',
            'stop_type',
            'service_time_minutes',
            'parking_capacity',
            'workshop_available',
            'fuel_station_available',
            'charging_available',
            'charger_count',
            'maintenance_bays',
            'operating_hours',
        ]

    def validate_location(self, value):
        if value is not None:
            from django.contrib.gis.geos import Point
            if not isinstance(value, Point):
                raise serializers.ValidationError(
                    'location must be a GeoJSON Point geometry'
                )
        return value

    def validate_operating_hours(self, value):
        return validate_operating_hours(value)


class BulkUploadSerializer(serializers.ModelSerializer):
    class Meta:
        model = BulkUpload
        ref_name = 'StopBulkUpload'
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
        ref_name = 'StopBulkUploadStatus'
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
