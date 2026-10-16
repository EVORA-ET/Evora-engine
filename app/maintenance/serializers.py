from datetime import timedelta

from rest_framework import serializers

from common.fields import OrgScopedPrimaryKeyField, request_org
from evidence import provenance as P
from evidence.models import EvidenceSource
from evidence.services import today_for
from maintenance.models import EventType, MaintenanceEvent, ServiceRequirement
from vehicles.models import Vehicle


class ServiceRequirementSerializer(serializers.ModelSerializer):
    organization_id = serializers.UUIDField(read_only=True)
    vehicle_id = OrgScopedPrimaryKeyField(
        queryset=Vehicle.objects.all(), source='vehicle')
    source_id = OrgScopedPrimaryKeyField(
        queryset=EvidenceSource.objects.all(), source='source')
    vehicle_registration = serializers.CharField(
        source='vehicle.registration_number', read_only=True)
    source = serializers.SerializerMethodField()

    class Meta:
        model = ServiceRequirement
        fields = [
            'id', 'organization_id', 'vehicle_id', 'vehicle_registration',
            'name', 'component',
            'interval_km', 'interval_days', 'warning_km', 'warning_days',
            'source_id', 'source', 'retired_at', 'retired_reason',
            'created_at',
        ]
        read_only_fields = ['id', 'organization_id', 'retired_at',
                            'retired_reason', 'created_at']

    def get_source(self, obj) -> dict | None:
        return obj.source.as_reference()

    def validate(self, attrs):
        e = {}
        ikm, idays = attrs.get('interval_km'), attrs.get('interval_days')
        if ikm is None and idays is None:
            e['interval_km'] = 'Give interval_km, interval_days or both.'
        if ikm is not None and not 0 < ikm <= 1_000_000:
            e['interval_km'] = 'Must be > 0 and <= 1,000,000 km.'
        if idays is not None and not 0 < idays <= 3650:
            e['interval_days'] = 'Must be 1-3650 days.'
        wkm, wdays = attrs.get('warning_km'), attrs.get('warning_days')
        if wkm is not None and (ikm is None or not 0 <= wkm < ikm):
            e['warning_km'] = 'Needs interval_km and must be below it.'
        if wdays is not None and (idays is None or not 0 <= wdays < idays):
            e['warning_days'] = 'Needs interval_days and must be below it.'
        if attrs['source'].verification_status == 'rejected':
            e['source_id'] = 'This source was rejected.'
        if e:
            raise serializers.ValidationError(e)
        return attrs


class MaintenanceEventSerializer(serializers.ModelSerializer):
    organization_id = serializers.UUIDField(read_only=True)
    vehicle_id = OrgScopedPrimaryKeyField(
        queryset=Vehicle.objects.all(), source='vehicle')
    requirement_id = OrgScopedPrimaryKeyField(
        queryset=ServiceRequirement.objects.all(), source='requirement',
        required=False, allow_null=True)
    resolves_id = OrgScopedPrimaryKeyField(
        queryset=MaintenanceEvent.objects.all(), source='resolves',
        required=False, allow_null=True)
    source_id = OrgScopedPrimaryKeyField(
        queryset=EvidenceSource.objects.all(), source='source',
        required=False, allow_null=True)
    vehicle_registration = serializers.CharField(
        source='vehicle.registration_number', read_only=True)

    class Meta:
        model = MaintenanceEvent
        fields = [
            'id', 'organization_id', 'vehicle_id', 'vehicle_registration',
            'event_type',
            'occurred_on', 'completed_on', 'odometer_km', 'requirement_id',
            'component', 'fault_code', 'fault_severity', 'resolves_id',
            'description', 'provider', 'cost', 'currency', 'downtime_hours',
            'origin', 'source_id', 'voided_at', 'void_reason', 'created_at',
        ]
        read_only_fields = ['id', 'organization_id', 'voided_at',
                            'void_reason', 'created_at']

    def validate(self, attrs):
        org = request_org(self)
        e = {}
        vehicle = attrs['vehicle']
        etype = attrs['event_type']
        # The organization's local date: an event entered just after local
        # midnight is not "in the future" because the server runs on UTC.
        today, _ = today_for(org)
        if attrs['occurred_on'] > today:
            e['occurred_on'] = 'Cannot be in the future.'
        if attrs['occurred_on'] < vehicle.purchase_date - \
                timedelta(days=366):
            e['occurred_on'] = 'More than a year before the vehicle purchase.'
        done = attrs.get('completed_on')
        if done and done < attrs['occurred_on']:
            e['completed_on'] = 'Must not be before occurred_on.'
        req = attrs.get('requirement')
        if req is not None:
            if req.vehicle_id != vehicle.pk:
                e['requirement_id'] = 'Belongs to another vehicle.'
            if etype in (EventType.FAULT_REPORT, EventType.REPAIR):
                e['requirement_id'] = ('Only services, inspections and '
                                       'replacements satisfy a requirement.')
        res = attrs.get('resolves')
        if res is not None:
            if res.event_type != EventType.FAULT_REPORT:
                e['resolves_id'] = 'Can only resolve a fault report.'
            elif res.vehicle_id != vehicle.pk:
                e['resolves_id'] = 'Fault belongs to another vehicle.'
            elif res.voided_at:
                e['resolves_id'] = 'That fault report was voided.'
            elif etype == EventType.FAULT_REPORT:
                e['resolves_id'] = 'A fault report cannot resolve a fault.'
        if etype == EventType.FAULT_REPORT and not (
                attrs.get('fault_code') or attrs.get('description')):
            e['description'] = 'Describe the fault or give its code.'
        if etype != EventType.FAULT_REPORT and attrs.get('fault_severity'):
            e['fault_severity'] = 'Only applies to fault reports.'
        for f in ('odometer_km', 'cost', 'downtime_hours'):
            if attrs.get(f) is not None and attrs[f] < 0:
                e[f] = 'Must not be negative.'
        if attrs.get('cost') is not None and attrs.get('currency') != \
                org.currency:
            e['currency'] = f'Must be {org.currency}.'
        origin = attrs['origin']
        if origin in P.ORIGINS_REQUIRING_SOURCE and not attrs.get('source'):
            e['source_id'] = f'A {origin} record must cite its source.'
        if origin in (P.MODELLED, P.ASSUMED, P.EXTERNAL):
            e['origin'] = ('Maintenance events are measured (a job card, '
                           'invoice or inspection report) or '
                           'organization_provided.')
        if e:
            raise serializers.ValidationError(e)
        return attrs


class RetireSerializer(serializers.Serializer):
    reason = serializers.CharField()
