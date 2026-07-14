from rest_framework import serializers

from depots.models import Depot
from job_templates.models import JobTemplate
from stops.models import Stop


class JobTemplateSerializer(serializers.ModelSerializer):
    organization_id = serializers.UUIDField(read_only=True)
    created_by_id = serializers.UUIDField(read_only=True, source='created_by.id', allow_null=True)
    source_depot_id = serializers.PrimaryKeyRelatedField(
        queryset=Depot.objects.all(), source='source_depot',
    )
    destination_stop_id = serializers.PrimaryKeyRelatedField(
        queryset=Stop.objects.all(), source='destination_stop',
    )

    class Meta:
        model = JobTemplate
        fields = [
            'id',
            'organization_id',
            'source_depot_id',
            'destination_stop_id',
            'name',
            'description',
            'is_recurring',
            'recurrence_rule',
            'is_permanent',
            'start_date',
            'end_date',
            'estimated_duration_minutes',
            'estimated_distance_km',
            'estimated_energy_kwh',
            'status',
            'created_by_id',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id', 'organization_id', 'created_by_id', 'created_at', 'updated_at',
        ]


class JobTemplateUpdateSerializer(serializers.ModelSerializer):
    source_depot_id = serializers.PrimaryKeyRelatedField(
        queryset=Depot.objects.all(), source='source_depot', required=False,
    )
    destination_stop_id = serializers.PrimaryKeyRelatedField(
        queryset=Stop.objects.all(), source='destination_stop', required=False,
    )

    class Meta:
        model = JobTemplate
        fields = [
            'source_depot_id',
            'destination_stop_id',
            'name',
            'description',
            'is_recurring',
            'recurrence_rule',
            'is_permanent',
            'start_date',
            'end_date',
            'estimated_duration_minutes',
            'estimated_distance_km',
            'estimated_energy_kwh',
            'status',
        ]
