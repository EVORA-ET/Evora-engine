from django.utils import timezone
from rest_framework import serializers

from common.fields import OrgScopedPrimaryKeyField, request_org
from depots.models import Depot
from evidence import provenance as P
from evidence import services as S
from evidence.models import (
    Assessment,
    EnergyBasis,
    EnergyRecord,
    EnergyRecordType,
    EvidenceSource,
    Observation,
)
from ev_migration.models import EVCandidate
from vehicles.models import FuelType, Vehicle


class EvidenceSourceSerializer(serializers.ModelSerializer):
    organization_id = serializers.UUIDField(read_only=True)
    verified_by_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = EvidenceSource
        fields = [
            'id', 'organization_id', 'title', 'source_type', 'publisher',
            'identifier', 'url', 'retrieval_method', 'retrieved_at',
            'published_on', 'effective_from', 'effective_to',
            'geographic_scope', 'applicability', 'methodology', 'notes',
            'verification_status', 'verified_by_id', 'verified_at',
            'verification_note', 'refresh_interval_days',
            'last_refresh_status', 'last_refresh_attempt_at',
            'last_successful_refresh_at', 'last_refresh_error', 'created_at',
        ]
        read_only_fields = [
            'id', 'organization_id', 'verification_status', 'verified_by_id',
            'verified_at', 'verification_note', 'last_refresh_status',
            'last_refresh_attempt_at', 'last_successful_refresh_at',
            'last_refresh_error', 'created_at',
        ]

    def validate(self, attrs):
        if attrs.get('retrieved_at') and attrs['retrieved_at'] > \
                timezone.now() + S.FUTURE_TOLERANCE:
            raise serializers.ValidationError(
                {'retrieved_at': 'Cannot be in the future.'})
        ef, et = attrs.get('effective_from'), attrs.get('effective_to')
        if ef and et and et < ef:
            raise serializers.ValidationError(
                {'effective_to': 'Must not be before effective_from.'})
        if attrs.get('retrieval_method') == 'api' and not attrs.get('url'):
            raise serializers.ValidationError(
                {'url': 'An API source must record its endpoint URL.'})
        return attrs


class SourceVerifySerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=['verified', 'rejected'])
    note = serializers.CharField(allow_blank=False)


class ObservationSerializer(serializers.ModelSerializer):
    organization_id = serializers.UUIDField(read_only=True)
    vehicle_id = serializers.UUIDField(read_only=True)
    depot_id = serializers.UUIDField(read_only=True)
    ev_candidate_id = serializers.UUIDField(read_only=True)
    source_id = serializers.UUIDField(read_only=True)
    supersedes_id = serializers.UUIDField(read_only=True)
    superseded_by_id = serializers.SerializerMethodField()
    subject = serializers.CharField(read_only=True)
    evidence_class = serializers.SerializerMethodField()
    stale = serializers.SerializerMethodField()
    stale_reason = serializers.SerializerMethodField()
    source = serializers.SerializerMethodField()

    class Meta:
        model = Observation
        fields = [
            'id', 'organization_id', 'kind', 'value', 'unit', 'subject',
            'vehicle_id', 'depot_id', 'ev_candidate_id', 'qualifiers',
            'origin', 'source_id', 'source', 'observed_at', 'period_start',
            'period_end', 'effective_from', 'effective_to',
            'validation_status', 'validation_notes', 'transformation', 'note',
            'supersedes_id', 'superseded_by_id', 'correction_reason',
            'evidence_class', 'stale', 'stale_reason', 'created_at',
        ]

    def _fresh(self, obj):
        cache = self.context.setdefault('_fresh', {})
        if obj.pk not in cache:
            as_of = self.context.get('as_of') or timezone.now().date()
            cache[obj.pk] = S.staleness(obj, as_of)
        return cache[obj.pk]

    def get_superseded_by_id(self, obj) -> str | None:
        try:
            return str(obj.superseded_by.pk)
        except Observation.superseded_by.RelatedObjectDoesNotExist:
            return None

    def get_stale(self, obj) -> bool:
        return self._fresh(obj)[0]

    def get_stale_reason(self, obj) -> str:
        return self._fresh(obj)[1]

    def get_evidence_class(self, obj) -> str:
        stale = self._fresh(obj)[0]
        return P.evidence_class(
            obj.origin, bool(obj.source and obj.source.is_verified), stale)

    def get_source(self, obj) -> dict | None:
        return obj.source.as_reference() if obj.source else None


class ObservationCreateSerializer(serializers.Serializer):
    kind = serializers.CharField()
    value = serializers.DecimalField(max_digits=20, decimal_places=6)
    unit = serializers.CharField(max_length=32)
    vehicle_id = OrgScopedPrimaryKeyField(
        queryset=Vehicle.objects.all(), source='vehicle', required=False,
        allow_null=True)
    depot_id = OrgScopedPrimaryKeyField(
        queryset=Depot.objects.all(), source='depot', required=False,
        allow_null=True)
    ev_candidate_id = OrgScopedPrimaryKeyField(
        queryset=EVCandidate.objects.all(), source='ev_candidate',
        required=False, allow_null=True)
    qualifiers = serializers.JSONField(required=False, default=dict)
    origin = serializers.ChoiceField(choices=P.ORIGINS)
    source_id = OrgScopedPrimaryKeyField(
        queryset=EvidenceSource.objects.all(), source='source',
        required=False, allow_null=True)
    observed_at = serializers.DateTimeField(required=False, allow_null=True)
    period_start = serializers.DateField(required=False, allow_null=True)
    period_end = serializers.DateField(required=False, allow_null=True)
    effective_from = serializers.DateField(required=False, allow_null=True)
    effective_to = serializers.DateField(required=False, allow_null=True)
    transformation = serializers.CharField(required=False, allow_blank=True,
                                           default='')
    note = serializers.CharField(required=False, allow_blank=True, default='')


class SupersedeSerializer(ObservationCreateSerializer):
    correction_reason = serializers.CharField()


class EnergyRecordSerializer(serializers.ModelSerializer):
    organization_id = serializers.UUIDField(read_only=True)
    vehicle_id = OrgScopedPrimaryKeyField(
        queryset=Vehicle.objects.all(), source='vehicle')
    depot_id = OrgScopedPrimaryKeyField(
        queryset=Depot.objects.all(), source='depot', required=False,
        allow_null=True)
    source_id = OrgScopedPrimaryKeyField(
        queryset=EvidenceSource.objects.all(), source='source',
        required=False, allow_null=True)
    unit = serializers.CharField(read_only=True)

    class Meta:
        model = EnergyRecord
        fields = [
            'id', 'organization_id', 'vehicle_id', 'record_type',
            'occurred_at', 'quantity', 'unit', 'energy_basis', 'is_full',
            'odometer_km', 'cost', 'currency', 'depot_id', 'origin',
            'source_id', 'note', 'voided_at', 'void_reason', 'created_at',
        ]
        read_only_fields = ['id', 'organization_id', 'voided_at',
                            'void_reason', 'created_at']

    def validate(self, attrs):
        org = request_org(self)
        errors = {}
        rtype = attrs['record_type']
        vehicle = attrs['vehicle']
        qty = attrs['quantity']
        if qty <= 0:
            errors['quantity'] = 'Must be greater than 0.'
        elif qty > 2000:
            errors['quantity'] = ('More than 2000 per record is not plausible '
                                  'for one fill or charging session.')
        if rtype == EnergyRecordType.FUEL_FILL:
            if attrs.get('energy_basis'):
                errors['energy_basis'] = 'Only applies to charging sessions.'
            if vehicle.fuel_type == FuelType.EV:
                errors['record_type'] = 'This vehicle is electric.'
        else:
            if attrs.get('energy_basis') not in EnergyBasis.values:
                errors['energy_basis'] = (
                    'Required for charging: "grid" (metered at the charger) '
                    'or "vehicle" (reported by the vehicle).')
            if vehicle.fuel_type != FuelType.EV:
                errors['record_type'] = 'This vehicle is not electric.'
        if attrs['occurred_at'] > timezone.now() + S.FUTURE_TOLERANCE:
            errors['occurred_at'] = 'Cannot be in the future.'
        odo = attrs.get('odometer_km')
        if odo is not None and odo < 0:
            errors['odometer_km'] = 'Must not be negative.'
        cost = attrs.get('cost')
        if cost is not None:
            if cost < 0:
                errors['cost'] = 'Must not be negative.'
            if attrs.get('currency') != org.currency:
                errors['currency'] = (f'Must be {org.currency}, the '
                                      'organization\'s currency.')
        origin = attrs['origin']
        if origin in P.ORIGINS_REQUIRING_SOURCE and not attrs.get('source'):
            errors['source_id'] = f'A {origin} record must cite its source.'
        if origin in (P.MODELLED, P.ASSUMED):
            errors['origin'] = ('Fuel and charging records must be measured or '
                                'organization_provided.')
        if errors:
            raise serializers.ValidationError(errors)
        return attrs


class VoidSerializer(serializers.Serializer):
    reason = serializers.CharField()


class AssessmentSerializer(serializers.ModelSerializer):
    organization_id = serializers.UUIDField(read_only=True)
    vehicle_id = serializers.UUIDField(read_only=True)
    ev_candidate_id = serializers.UUIDField(read_only=True)
    created_by_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = Assessment
        fields = [
            'id', 'organization_id', 'kind', 'method_version', 'vehicle_id',
            'ev_candidate_id', 'subject_snapshot', 'status',
            'evidence_level', 'evidence_reasons', 'input_snapshot',
            'parameters', 'result', 'missing_inputs', 'warnings',
            'fingerprint', 'created_by_id', 'created_at',
        ]
        read_only_fields = fields


class AssessmentSummarySerializer(AssessmentSerializer):
    class Meta(AssessmentSerializer.Meta):
        fields = [
            'id', 'kind', 'method_version', 'vehicle_id', 'ev_candidate_id',
            'subject_snapshot', 'status', 'evidence_level', 'missing_inputs',
            'fingerprint', 'created_at',
        ]
        read_only_fields = fields
