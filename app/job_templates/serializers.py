from rest_framework import serializers

from common.fields import OrgScopedPrimaryKeyField
from depots.models import Depot
from job_templates.models import JobTemplate


class JobTemplateRulesMixin:
    def validate(self, attrs):
        start = attrs.get(
            'start_date', getattr(self.instance, 'start_date', None)
        )
        end = attrs.get('end_date', getattr(self.instance, 'end_date', None))
        if start and end and end < start:
            raise serializers.ValidationError(
                {'end_date': 'End date must not be before the start date.'}
            )
        for field in (
            'estimated_duration_minutes',
            'estimated_distance_km',
            'estimated_energy_kwh',
        ):
            value = attrs.get(field)
            if value is not None and value < 0:
                raise serializers.ValidationError(
                    {field: 'Must not be negative.'}
                )
        if attrs.get('is_recurring') and not attrs.get(
            'recurrence_rule', getattr(self.instance, 'recurrence_rule', '')
        ):
            raise serializers.ValidationError(
                {
                    'recurrence_rule': 'Describe the recurrence for a recurring route.'
                }
            )
        return attrs


class JobTemplateSerializer(
    JobTemplateRulesMixin, serializers.ModelSerializer
):
    organization_id = serializers.UUIDField(read_only=True)
    created_by_id = serializers.UUIDField(
        read_only=True, source='created_by.id', allow_null=True
    )
    source_depot_id = OrgScopedPrimaryKeyField(
        queryset=Depot.objects.all(),
        source='source_depot',
    )
    destination_depot_id = OrgScopedPrimaryKeyField(
        queryset=Depot.objects.all(),
        source='destination_depot',
        required=False,
        allow_null=True,
    )
    source_depot_name = serializers.CharField(
        source='source_depot.name', read_only=True
    )
    destination_depot_name = serializers.CharField(
        source='destination_depot.name', read_only=True, default=None
    )

    class Meta:
        model = JobTemplate
        fields = [
            'id',
            'organization_id',
            'source_depot_id',
            'destination_depot_id',
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
            'source_depot_name',
            'destination_depot_name',
        ]
        read_only_fields = [
            'id',
            'organization_id',
            'created_by_id',
            'created_at',
            'updated_at',
        ]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        stats = self.context.get('template_stats')
        if stats is not None:
            data.update(
                stats.get(
                    instance.pk, {'job_count': 0, 'upcoming_job_count': 0}
                )
            )
        return data


class JobTemplateUpdateSerializer(
    JobTemplateRulesMixin, serializers.ModelSerializer
):
    source_depot_id = OrgScopedPrimaryKeyField(
        queryset=Depot.objects.all(),
        source='source_depot',
        required=False,
    )
    destination_depot_id = OrgScopedPrimaryKeyField(
        queryset=Depot.objects.all(),
        source='destination_depot',
        required=False,
        allow_null=True,
    )

    class Meta:
        model = JobTemplate
        fields = [
            'source_depot_id',
            'destination_depot_id',
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
