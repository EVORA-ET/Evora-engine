from datetime import date

from rest_framework import serializers
from users.models import User

from common.fields import OrgScopedPrimaryKeyField
from drivers.models import BulkUpload, Driver
from job_instances.services import summarise_current_job

MIN_DRIVER_AGE = 18


class DriverRulesMixin:
    """Field rules shared by create and update."""

    def validate_date_of_birth(self, value):
        today = date.today()
        if value > today:
            raise serializers.ValidationError('Date of birth cannot be in the future.')
        age = today.year - value.year - ((today.month, today.day) < (value.month, value.day))
        if age < MIN_DRIVER_AGE:
            raise serializers.ValidationError(
                f'A driver must be at least {MIN_DRIVER_AGE} years old.')
        return value

    def validate_experience_years(self, value):
        if value < 0:
            raise serializers.ValidationError('Experience cannot be negative.')
        if value > 60:
            raise serializers.ValidationError('Experience looks too high (max 60 years).')
        return value

    def validate_driver_code(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('This field may not be blank.')
        return value

    def validate(self, attrs):
        dob = attrs.get('date_of_birth') or getattr(self.instance, 'date_of_birth', None)
        expiry = attrs.get('license_expiry') or getattr(self.instance, 'license_expiry', None)
        if dob and expiry and expiry <= dob:
            raise serializers.ValidationError({
                'license_expiry': 'Licence expiry must be after the date of birth.'})
        return attrs


class DriverSerializer(DriverRulesMixin, serializers.ModelSerializer):
    organization_id = serializers.UUIDField(read_only=True)
    user_id = OrgScopedPrimaryKeyField(
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


class DriverOpsSerializer(DriverSerializer):
    """Driver plus current assignment, for Operations. `open_job_count` and
    `current_job` appear only when the view supplies `open_jobs`."""

    class Meta(DriverSerializer.Meta):
        pass

    def to_representation(self, instance):
        data = super().to_representation(instance)
        open_jobs = self.context.get('open_jobs')
        if open_jobs is not None:
            jobs = open_jobs.get(instance.pk, [])
            data['open_job_count'] = len(jobs)
            data['current_job'] = summarise_current_job(jobs)
        return data


class DriverUpdateSerializer(DriverRulesMixin, serializers.ModelSerializer):
    user_id = OrgScopedPrimaryKeyField(
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
