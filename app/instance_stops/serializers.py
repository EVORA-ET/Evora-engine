from rest_framework import serializers

from instance_stops.models import InstanceStop, InstanceStopStatus
from job_instances.models import JobInstance
from stops.models import Stop


class InstanceStopSerializer(serializers.ModelSerializer):
    job_instance_id = serializers.PrimaryKeyRelatedField(
        queryset=JobInstance.objects.all(), source='job_instance',
    )
    stop_id = serializers.PrimaryKeyRelatedField(
        queryset=Stop.objects.all(), source='stop',
    )

    class Meta:
        model = InstanceStop
        fields = [
            'id',
            'job_instance_id',
            'stop_id',
            'sequence_number',
            'estimated_arrival',
            'actual_arrival',
            'actual_departure',
            'status',
            'delay_minutes',
            'notes',
            'created_at',
        ]
        read_only_fields = [
            'id', 'created_at',
        ]

    def validate_sequence_number(self, value):
        if value < 0:
            raise serializers.ValidationError(
                'sequence_number must be 0 or greater.'
            )
        return value

    def validate(self, attrs):
        actual_arrival = attrs.get('actual_arrival')
        actual_departure = attrs.get('actual_departure')
        if actual_arrival and actual_departure and actual_arrival >= actual_departure:
            raise serializers.ValidationError(
                'actual_arrival must be before actual_departure.'
            )
        return attrs


class InstanceStopUpdateSerializer(serializers.ModelSerializer):
    job_instance_id = serializers.PrimaryKeyRelatedField(
        queryset=JobInstance.objects.all(), source='job_instance',
        required=False,
    )
    stop_id = serializers.PrimaryKeyRelatedField(
        queryset=Stop.objects.all(), source='stop',
        required=False,
    )

    class Meta:
        model = InstanceStop
        fields = [
            'job_instance_id',
            'stop_id',
            'sequence_number',
            'estimated_arrival',
            'actual_arrival',
            'actual_departure',
            'status',
            'delay_minutes',
            'notes',
        ]

    def validate_sequence_number(self, value):
        if value < 0:
            raise serializers.ValidationError(
                'sequence_number must be 0 or greater.'
            )
        return value

    def validate(self, attrs):
        actual_arrival = attrs.get('actual_arrival')
        actual_departure = attrs.get('actual_departure')
        if actual_arrival and actual_departure and actual_arrival >= actual_departure:
            raise serializers.ValidationError(
                'actual_arrival must be before actual_departure.'
            )
        return attrs
