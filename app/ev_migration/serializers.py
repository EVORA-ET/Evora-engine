from rest_framework import serializers

from common.fields import OrgScopedPrimaryKeyField
from evidence import provenance as P
from evidence.models import Assessment, AssessmentKind, EvidenceSource
from ev_migration.models import EVCandidate, MigrationDecision
from vehicles.models import Vehicle


class EVCandidateSerializer(serializers.ModelSerializer):
    organization_id = serializers.UUIDField(read_only=True)
    source_id = OrgScopedPrimaryKeyField(
        queryset=EvidenceSource.objects.all(), source='source')
    source = serializers.SerializerMethodField()
    label = serializers.CharField(read_only=True)

    class Meta:
        model = EVCandidate
        fields = [
            'id', 'organization_id', 'label', 'manufacturer', 'model',
            'variant', 'vehicle_type', 'usable_battery_kwh',
            'energy_consumption_kwh_per_km', 'consumption_basis',
            'consumption_source_type', 'consumption_conditions', 'payload_kg',
            'gvw_kg', 'max_ac_charge_kw', 'max_dc_charge_kw',
            'rated_range_km', 'rated_range_cycle', 'origin', 'source_id',
            'source', 'created_at',
        ]
        read_only_fields = ['id', 'organization_id', 'label', 'created_at']

    def get_source(self, obj) -> dict | None:
        return obj.source.as_reference()

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['label'] = instance.label()
        return data

    def validate(self, attrs):
        e = {}
        if not 0 < attrs['usable_battery_kwh'] <= 2000:
            e['usable_battery_kwh'] = 'Must be > 0 and <= 2000 kWh.'
        if not 0.02 <= attrs['energy_consumption_kwh_per_km'] <= 5:
            e['energy_consumption_kwh_per_km'] = (
                'Must be 0.02-5 kWh/km. EVORA stores kWh per km; convert '
                'Wh/km or km/kWh before entry.')
        if attrs['payload_kg'] < 0:
            e['payload_kg'] = 'Must not be negative.'
        for f in ('max_ac_charge_kw', 'max_dc_charge_kw', 'rated_range_km',
                  'gvw_kg'):
            if attrs.get(f) is not None and attrs[f] <= 0:
                e[f] = 'Must be greater than 0.'
        origin = attrs['origin']
        if origin not in (P.EXTERNAL, P.MEASURED):
            e['origin'] = ('A candidate specification must come from an '
                           'external document (external) or a trial/fleet '
                           'measurement (measured).')
        cst = attrs['consumption_source_type']
        if cst == 'manufacturer_rated' and origin != P.EXTERNAL:
            e['origin'] = 'A manufacturer-rated figure has origin external.'
        if cst != 'manufacturer_rated' and origin != P.MEASURED:
            e['origin'] = 'A measured consumption figure has origin measured.'
        if attrs['source'].verification_status == 'rejected':
            e['source_id'] = 'This source was rejected.'
        if e:
            raise serializers.ValidationError(e)
        return attrs


class AssessmentRequestSerializer(serializers.Serializer):
    vehicle_id = OrgScopedPrimaryKeyField(
        queryset=Vehicle.objects.all(), source='vehicle')
    ev_candidate_id = OrgScopedPrimaryKeyField(
        queryset=EVCandidate.objects.all(), source='ev_candidate')


class MigrationDecisionSerializer(serializers.ModelSerializer):
    organization_id = serializers.UUIDField(read_only=True)
    vehicle_id = OrgScopedPrimaryKeyField(
        queryset=Vehicle.objects.all(), source='vehicle')
    ev_candidate_id = OrgScopedPrimaryKeyField(
        queryset=EVCandidate.objects.all(), source='ev_candidate',
        required=False, allow_null=True)
    readiness_assessment_id = OrgScopedPrimaryKeyField(
        queryset=Assessment.objects.all(), source='readiness_assessment',
        required=False, allow_null=True)
    tco_assessment_id = OrgScopedPrimaryKeyField(
        queryset=Assessment.objects.all(), source='tco_assessment',
        required=False, allow_null=True)
    emissions_assessment_id = OrgScopedPrimaryKeyField(
        queryset=Assessment.objects.all(), source='emissions_assessment',
        required=False, allow_null=True)
    decided_by_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = MigrationDecision
        fields = [
            'id', 'organization_id', 'vehicle_id', 'vehicle_snapshot',
            'ev_candidate_id', 'decision', 'readiness_assessment_id',
            'tco_assessment_id', 'emissions_assessment_id', 'rationale',
            'decided_by_id', 'decided_at',
        ]
        read_only_fields = ['id', 'organization_id', 'vehicle_snapshot',
                            'decided_by_id', 'decided_at']

    def validate(self, attrs):
        vehicle = attrs['vehicle']
        cand = attrs.get('ev_candidate')
        linked = {
            'readiness_assessment': AssessmentKind.EV_READINESS,
            'tco_assessment': AssessmentKind.TCO,
            'emissions_assessment': AssessmentKind.EMISSIONS,
        }
        errors = {}
        if not any(attrs.get(k) for k in linked):
            errors['readiness_assessment_id'] = (
                'Link at least one assessment that informed the decision.')
        for field, kind in linked.items():
            a = attrs.get(field)
            if a is None:
                continue
            if a.kind != kind:
                errors[f'{field}_id'] = f'Must be a {kind} assessment.'
            elif a.vehicle_id != vehicle.pk:
                errors[f'{field}_id'] = 'Assessment is for another vehicle.'
            elif cand is not None and a.ev_candidate_id != cand.pk:
                errors[f'{field}_id'] = 'Assessment is for another candidate.'
        if attrs['decision'] == 'approve_transition':
            r = attrs.get('readiness_assessment')
            if cand is None:
                errors['ev_candidate_id'] = 'Approving needs a candidate.'
            if r is None or r.result.get('outcome') != 'meets_requirements':
                errors['decision'] = (
                    'A transition can only be approved with a readiness '
                    'assessment in which every mandatory operational check '
                    'passed.')
        if errors:
            raise serializers.ValidationError(errors)
        return attrs
