from rest_framework import serializers

from job_instances.models import JobInstance, JobInstanceStatus
from job_templates.models import JobTemplate
from vehicles.models import Vehicle
from drivers.models import Driver


class JobInstanceSerializer(serializers.ModelSerializer):
    organization_id = serializers.UUIDField(read_only=True)
    job_template_id = serializers.PrimaryKeyRelatedField(
        queryset=JobTemplate.objects.all(), source='job_template',
    )
    vehicle_id = serializers.PrimaryKeyRelatedField(
        queryset=Vehicle.objects.all(), source='vehicle',
    )
    driver_id = serializers.PrimaryKeyRelatedField(
        queryset=Driver.objects.all(), source='driver',
    )

    class Meta:
        model = JobInstance
        fields = [
            'id',
            'organization_id',
            'job_template_id',
            'vehicle_id',
            'driver_id',
            'scheduled_start_at',
            'scheduled_end_at',
            'actual_start_at',
            'actual_end_at',
            'status',
            'completion_percentage',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id', 'organization_id', 'created_at', 'updated_at',
        ]

    def validate_completion_percentage(self, value):
        if value < 0 or value > 100:
            raise serializers.ValidationError(
                'completion_percentage must be between 0 and 100.'
            )
        return value

    def validate(self, attrs):
        scheduled_start = attrs.get('scheduled_start_at')
        scheduled_end = attrs.get('scheduled_end_at')
        if scheduled_start and scheduled_end and scheduled_start >= scheduled_end:
            raise serializers.ValidationError(
                'scheduled_start_at must be before scheduled_end_at.'
            )
        actual_start = attrs.get('actual_start_at')
        actual_end = attrs.get('actual_end_at')
        if actual_start and actual_end and actual_start >= actual_end:
            raise serializers.ValidationError(
                'actual_start_at must be before actual_end_at.'
            )
        return attrs


class JobInstanceUpdateSerializer(serializers.ModelSerializer):
    job_template_id = serializers.PrimaryKeyRelatedField(
        queryset=JobTemplate.objects.all(), source='job_template',
        required=False,
    )
    vehicle_id = serializers.PrimaryKeyRelatedField(
        queryset=Vehicle.objects.all(), source='vehicle',
        required=False, allow_null=True,
    )
    driver_id = serializers.PrimaryKeyRelatedField(
        queryset=Driver.objects.all(), source='driver',
        required=False, allow_null=True,
    )

    class Meta:
        model = JobInstance
        fields = [
            'job_template_id',
            'vehicle_id',
            'driver_id',
            'scheduled_start_at',
            'scheduled_end_at',
            'actual_start_at',
            'actual_end_at',
            'status',
            'completion_percentage',
        ]

    def validate_completion_percentage(self, value):
        if value < 0 or value > 100:
            raise serializers.ValidationError(
                'completion_percentage must be between 0 and 100.'
            )
        return value

    def validate(self, attrs):
        scheduled_start = attrs.get('scheduled_start_at')
        scheduled_end = attrs.get('scheduled_end_at')
        if scheduled_start and scheduled_end and scheduled_start >= scheduled_end:
            raise serializers.ValidationError(
                'scheduled_start_at must be before scheduled_end_at.'
            )
        actual_start = attrs.get('actual_start_at')
        actual_end = attrs.get('actual_end_at')
        if actual_start and actual_end and actual_start >= actual_end:
            raise serializers.ValidationError(
                'actual_start_at must be before actual_end_at.'
            )
        return attrs
