from rest_framework import serializers

from common.fields import OrgScopedPrimaryKeyField
from drivers.models import Driver
from job_instances.models import JobInstance
from job_templates.models import JobTemplate
from vehicles.models import Vehicle


def assignment_state(job):
    if job.vehicle_id and job.driver_id:
        return 'assigned'
    if not job.vehicle_id and not job.driver_id:
        return 'unassigned'
    return 'missing_driver' if job.vehicle_id else 'missing_vehicle'


class JobInstanceSerializer(serializers.ModelSerializer):
    """Read shape for jobs. Writes go through the dedicated serializers and
    services; this one only presents data (the Dashboard consumes it too, so
    existing fields are unchanged and new ones are additive)."""

    organization_id = serializers.UUIDField(read_only=True)
    job_template_id = serializers.UUIDField(read_only=True)
    vehicle_id = serializers.UUIDField(read_only=True, allow_null=True)
    driver_id = serializers.UUIDField(read_only=True, allow_null=True)

    job_template_name = serializers.CharField(
        source='job_template.name', read_only=True
    )
    source_depot_id = serializers.UUIDField(
        source='job_template.source_depot_id', read_only=True
    )
    source_depot_name = serializers.CharField(
        source='job_template.source_depot.name', read_only=True
    )
    destination_depot_id = serializers.UUIDField(
        source='job_template.destination_depot_id',
        read_only=True,
        default=None,
    )
    destination_depot_name = serializers.CharField(
        source='job_template.destination_depot.name',
        read_only=True,
        default=None,
    )
    vehicle_registration_number = serializers.CharField(
        source='vehicle.registration_number', read_only=True, default=None
    )
    vehicle_label = serializers.SerializerMethodField()
    driver_name = serializers.CharField(
        source='driver.name', read_only=True, default=None
    )
    driver_code = serializers.CharField(
        source='driver.driver_code', read_only=True, default=None
    )
    assignment_state = serializers.SerializerMethodField()

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
            'job_template_name',
            'source_depot_id',
            'source_depot_name',
            'destination_depot_id',
            'destination_depot_name',
            'vehicle_registration_number',
            'vehicle_label',
            'driver_name',
            'driver_code',
            'assignment_state',
        ]
        read_only_fields = fields

    def get_vehicle_label(self, job):
        if not job.vehicle_id:
            return None
        return f'{job.vehicle.manufacturer} {job.vehicle.model}'.strip()

    def get_assignment_state(self, job):
        return assignment_state(job)


class JobInstanceDetailSerializer(JobInstanceSerializer):
    template = serializers.SerializerMethodField()
    stops = serializers.SerializerMethodField()

    class Meta(JobInstanceSerializer.Meta):
        fields = JobInstanceSerializer.Meta.fields + ['template', 'stops']
        read_only_fields = fields

    def get_template(self, job):
        t = job.job_template
        return {
            'id': t.pk,
            'name': t.name,
            'description': t.description,
            'status': t.status,
            'is_recurring': t.is_recurring,
            'estimated_duration_minutes': t.estimated_duration_minutes,
            'estimated_distance_km': t.estimated_distance_km,
            'estimated_energy_kwh': t.estimated_energy_kwh,
        }

    def get_stops(self, job):
        return [
            {
                'id': s.pk,
                'stop_id': s.stop_id,
                'stop_name': s.stop.name,
                'stop_address': s.stop.address,
                'stop_type': s.stop.stop_type,
                'sequence_number': s.sequence_number,
                'status': s.status,
                'estimated_arrival': s.estimated_arrival,
                'actual_arrival': s.actual_arrival,
                'actual_departure': s.actual_departure,
                'delay_minutes': s.delay_minutes,
                'notes': s.notes,
            }
            for s in job.instance_stops.all()
        ]


class JobInstanceCreateSerializer(serializers.Serializer):
    """Creating a job: route + schedule, optionally a vehicle and driver.

    Status, actual times and completion are not accepted here; a new job is
    PENDING, or ASSIGNED when both resources are supplied and validate.
    """

    job_template_id = OrgScopedPrimaryKeyField(
        queryset=JobTemplate.objects.all(), source='job_template'
    )
    scheduled_start_at = serializers.DateTimeField()
    scheduled_end_at = serializers.DateTimeField(
        required=False, allow_null=True
    )
    vehicle_id = OrgScopedPrimaryKeyField(
        queryset=Vehicle.objects.all(),
        source='vehicle',
        required=False,
        allow_null=True,
    )
    driver_id = OrgScopedPrimaryKeyField(
        queryset=Driver.objects.all(),
        source='driver',
        required=False,
        allow_null=True,
    )


class JobInstanceUpdateSerializer(serializers.Serializer):
    """Schedule/route/progress edits. Vehicle, driver and status have their own
    endpoints (/assign, /status) so their rules cannot be bypassed here."""

    job_template_id = OrgScopedPrimaryKeyField(
        queryset=JobTemplate.objects.all(),
        source='job_template',
        required=False,
    )
    scheduled_start_at = serializers.DateTimeField(required=False)
    scheduled_end_at = serializers.DateTimeField(required=False)
    completion_percentage = serializers.IntegerField(
        required=False, min_value=0, max_value=100
    )


class AssignSerializer(serializers.Serializer):
    vehicle_id = serializers.UUIDField(required=False, allow_null=True)
    driver_id = serializers.UUIDField(required=False, allow_null=True)

    def validate(self, attrs):
        if not attrs and not self.initial_data:
            raise serializers.ValidationError(
                'Provide vehicle_id and/or driver_id (null to unassign).'
            )
        return attrs


class StatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=[
            'IN_PROGRESS',
            'COMPLETED',
            'CANCELLED',
            'ASSIGNED',
            'PENDING',
        ]
    )
