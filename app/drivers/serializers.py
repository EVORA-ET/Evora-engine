from rest_framework import serializers
from users.models import User

from drivers.models import BulkUpload, Driver


class DriverSerializer(serializers.ModelSerializer):
    organization_id = serializers.UUIDField(read_only=True)
    user_id = serializers.PrimaryKeyRelatedField(
        source='user', read_only=False,
        queryset=User.objects.all(),
        required=False, allow_null=True,
    )

    class Meta:
        model = Driver
        fields = [
            'id',
            'organization_id',
            'user_id',
            'driver_code',
            'name',
            'date_of_birth',
            'license_number',
            'license_class',
            'license_expiry',
            'experience_years',
            'job_id',
            'status',
            'shift',
            'emergency_contact_name',
            'emergency_contact_phone',
            'languages',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id', 'organization_id', 'created_at', 'updated_at',
        ]


class DriverUpdateSerializer(serializers.ModelSerializer):
    user_id = serializers.PrimaryKeyRelatedField(
        source='user', read_only=False,
        queryset=User.objects.all(),
        required=False, allow_null=True,
    )

    class Meta:
        model = Driver
        fields = [
            'driver_code',
            'name',
            'date_of_birth',
            'license_number',
            'license_class',
            'license_expiry',
            'experience_years',
            'job_id',
            'status',
            'shift',
            'emergency_contact_name',
            'emergency_contact_phone',
            'languages',
            'user_id',
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
