from rest_framework import serializers

from common.fields import request_org
from fleets.models import Fleet


class FleetSerializer(serializers.ModelSerializer):
    organization_id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(max_length=255)
    description = serializers.CharField(
        required=False, allow_blank=True, default=''
    )
    # Counts come from grouped queries in the view (one query per page).
    vehicle_count = serializers.IntegerField(read_only=True, default=0)
    ev_count = serializers.IntegerField(read_only=True, default=0)
    ice_count = serializers.IntegerField(read_only=True, default=0)
    active_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Fleet
        fields = [
            'id',
            'organization_id',
            'name',
            'description',
            'vehicle_count',
            'ev_count',
            'ice_count',
            'active_count',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id',
            'organization_id',
            'created_at',
            'updated_at',
        ]

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('This field may not be blank.')
        org = request_org(self)
        if org is not None:
            clash = Fleet.objects.filter(organization=org, name__iexact=value)
            if self.instance is not None:
                clash = clash.exclude(pk=self.instance.pk)
            if clash.exists():
                raise serializers.ValidationError(
                    'A fleet with this name already exists.'
                )
        return value

    def to_representation(self, instance):
        data = super().to_representation(instance)
        stats = self.context.get('fleet_stats')
        if stats is not None:
            data.update(
                stats.get(
                    instance.pk,
                    {
                        'vehicle_count': 0,
                        'ev_count': 0,
                        'ice_count': 0,
                        'active_count': 0,
                    },
                )
            )
        return data


class FleetMembershipSerializer(serializers.Serializer):
    add = serializers.ListField(
        child=serializers.UUIDField(), required=False, allow_empty=True
    )
    remove = serializers.ListField(
        child=serializers.UUIDField(), required=False, allow_empty=True
    )
