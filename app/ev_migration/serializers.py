from rest_framework import serializers

from common.fields import OrgScopedPrimaryKeyField, request_org
from evidence import provenance as P
from evidence.models import (
    Assessment,
    AssessmentKind,
    AssessmentStatus,
    EvidenceSource,
)
from ev_migration.models import (
    EVModel,
    EVCandidate,
    MigrationDecision,
    MigrationPlan,
    MigrationPlanItem,
    PlanItemStatus,
    PlanItemStatusChange,
)
from ev_migration.planning import item_flags, validate_links
from vehicles.models import Vehicle

# Formal approval needs at least this much evidence behind the readiness
# result. Lower-coverage assessments stay available for exploration and can
# still back a "defer" or "reject" decision.
APPROVAL_MIN_EVIDENCE_LEVELS = (P.HIGH, P.MODERATE)


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
            'source', 'catalogue_model_id', 'created_at',
        ]
        read_only_fields = ['id', 'organization_id', 'label',
                            'catalogue_model_id', 'created_at']

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
            if r is None or r.status != AssessmentStatus.COMPLETED or \
                    r.result.get('outcome') != 'meets_requirements':
                errors['decision'] = (
                    'A transition can only be approved with a readiness '
                    'assessment in which every mandatory operational check '
                    'passed.')
            elif r.evidence_level not in APPROVAL_MIN_EVIDENCE_LEVELS:
                errors['decision'] = (
                    f'The readiness assessment has {r.evidence_level} evidence '
                    'coverage; formal approval needs moderate or high. It can '
                    'still support a "defer" or "reject" decision. Improve the '
                    'evidence (replace assumed or stale inputs) and re-run the '
                    'assessment to approve.')
        if errors:
            raise serializers.ValidationError(errors)
        return attrs


# ---------------------------------------------------------------------------
# Migration planning
# ---------------------------------------------------------------------------


def _assessment_ref(a):
    if a is None:
        return None
    r = a.result or {}
    return {
        'id': str(a.pk), 'kind': a.kind, 'status': a.status,
        'evidence_level': a.evidence_level,
        'outcome': r.get('outcome'),
        'net_present_savings': r.get('net_present_savings'),
        'currency': r.get('currency'),
        'created_at': a.created_at.isoformat(),
    }


class PlanItemStatusChangeSerializer(serializers.ModelSerializer):
    changed_by_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = PlanItemStatusChange
        fields = ['from_status', 'to_status', 'note', 'changed_by_id',
                  'changed_at']
        read_only_fields = fields


class MigrationPlanItemSerializer(serializers.ModelSerializer):
    plan_id = serializers.UUIDField(read_only=True)
    plan_name = serializers.CharField(source='plan.name', read_only=True)
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
    decision_id = OrgScopedPrimaryKeyField(
        queryset=MigrationDecision.objects.all(), source='decision',
        required=False, allow_null=True)
    vehicle = serializers.SerializerMethodField()
    ev_candidate_label = serializers.SerializerMethodField()
    readiness = serializers.SerializerMethodField()
    tco = serializers.SerializerMethodField()
    decision = serializers.SerializerMethodField()
    flags = serializers.SerializerMethodField()
    status_history = PlanItemStatusChangeSerializer(many=True, read_only=True)

    class Meta:
        model = MigrationPlanItem
        fields = [
            'id', 'plan_id', 'plan_name', 'vehicle_id', 'vehicle',
            'ev_candidate_id', 'ev_candidate_label', 'phase', 'target_date',
            'estimated_cost', 'readiness_assessment_id', 'readiness',
            'tco_assessment_id', 'tco', 'decision_id', 'decision', 'status',
            'completed_on', 'notes', 'flags', 'status_history', 'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'plan_id', 'plan_name', 'status',
                            'completed_on', 'created_at', 'updated_at']

    def get_vehicle(self, obj) -> dict:
        v = obj.vehicle
        return {'id': str(v.pk), 'registration_number': v.registration_number,
                'manufacturer': v.manufacturer, 'model': v.model,
                'vehicle_type': v.vehicle_type, 'fuel_type': v.fuel_type,
                'depot_id': str(v.depot_id) if v.depot_id else None}

    def get_ev_candidate_label(self, obj) -> str | None:
        return obj.ev_candidate.label() if obj.ev_candidate else None

    def get_readiness(self, obj) -> dict | None:
        return _assessment_ref(obj.readiness_assessment)

    def get_tco(self, obj) -> dict | None:
        return _assessment_ref(obj.tco_assessment)

    def get_decision(self, obj) -> dict | None:
        d = obj.decision
        if d is None:
            return None
        return {'id': str(d.pk), 'decision': d.decision,
                'decided_at': d.decided_at.isoformat()}

    def get_flags(self, obj) -> list:
        return item_flags(obj)

    def validate(self, attrs):
        org = request_org(self)
        e = {}
        if self.instance is not None:
            if 'vehicle' in attrs and attrs['vehicle'].pk != \
                    self.instance.vehicle_id:
                e['vehicle_id'] = ('The vehicle of a plan item cannot be '
                                   'changed; remove the item and add another.')
            if self.instance.status in ('completed', 'cancelled'):
                locked = set(attrs) - {'notes'}
                if locked:
                    e['status'] = (f'A {self.instance.status} item can only '
                                   'have its notes edited.')
        vehicle = attrs.get('vehicle') or self.instance.vehicle
        phase = attrs.get('phase')
        if phase is not None and not 1 <= phase <= 50:
            e['phase'] = 'Use a phase number from 1 to 50.'
        cost = attrs.get('estimated_cost')
        if cost is not None and cost < 0:
            e['estimated_cost'] = 'Must not be negative.'
        plan = self.context.get('plan') or getattr(self.instance, 'plan',
                                                   None)
        td = attrs.get('target_date')
        if td and plan is not None:
            if plan.start_date and td < plan.start_date:
                e['target_date'] = 'Before the plan start date.'
            if plan.end_date and td > plan.end_date:
                e['target_date'] = 'After the plan end date.'
        e.update(validate_links(attrs, vehicle=vehicle, org=org,
                                instance=self.instance))
        if e:
            raise serializers.ValidationError(e)
        return attrs


class MigrationPlanSerializer(serializers.ModelSerializer):
    organization_id = serializers.UUIDField(read_only=True)
    summary = serializers.SerializerMethodField()

    class Meta:
        model = MigrationPlan
        fields = [
            'id', 'organization_id', 'name', 'description', 'status',
            'start_date', 'end_date', 'budget_amount', 'currency',
            'assumptions', 'target_ev_share_pct', 'summary', 'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'organization_id', 'currency', 'summary',
                            'created_at', 'updated_at']

    def get_summary(self, obj) -> dict:
        items = list(obj.items.all())
        by_status = {s: 0 for s in PlanItemStatus.values}
        for i in items:
            by_status[i.status] += 1
        costed = [i.estimated_cost for i in items
                  if i.estimated_cost is not None
                  and i.status != PlanItemStatus.CANCELLED]
        active = len(items) - by_status['cancelled']
        return {
            'item_count': len(items),
            'by_status': by_status,
            'completed_fraction': (round(by_status['completed'] / active, 4)
                                   if active else None),
            'completed_fraction_note': ('Completed items / items not '
                                        'cancelled, from recorded statuses.'),
            'estimated_cost_total': (str(sum(costed)) if costed else None),
            'items_without_cost': sum(
                1 for i in items if i.estimated_cost is None
                and i.status != PlanItemStatus.CANCELLED),
            'phases': sorted({i.phase for i in items}),
        }

    def validate(self, attrs):
        e = {}
        name = attrs.get('name')
        if name is not None and not name.strip():
            e['name'] = 'This field may not be blank.'
        start = attrs.get('start_date', getattr(self.instance, 'start_date',
                                                None))
        end = attrs.get('end_date', getattr(self.instance, 'end_date', None))
        if start and end and end < start:
            e['end_date'] = 'Must not be before the start date.'
        b = attrs.get('budget_amount')
        if b is not None and b < 0:
            e['budget_amount'] = 'Must not be negative.'
        t = attrs.get('target_ev_share_pct')
        if t is not None and not 0 < t <= 100:
            e['target_ev_share_pct'] = 'Must be above 0 and at most 100%.'
        if e:
            raise serializers.ValidationError(e)
        return attrs


class PlanItemStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=PlanItemStatus.values)
    note = serializers.CharField(required=False, allow_blank=True,
                                 default='')
    completed_on = serializers.DateField(required=False, allow_null=True)


class EVModelSerializer(serializers.ModelSerializer):
    """A catalogue entry with what an assessment can and cannot use."""

    label = serializers.CharField(read_only=True)
    consumption = serializers.SerializerMethodField()
    missing_specs = serializers.SerializerMethodField()
    assessable = serializers.SerializerMethodField()

    class Meta:
        model = EVModel
        fields = [
            'id', 'label', 'manufacturer', 'model', 'variant',
            'vehicle_type', 'body', 'battery_capacity_kwh',
            'battery_capacity_is_usable', 'energy_consumption_kwh_per_km',
            'published_range_km', 'range_basis', 'range_cycle', 'payload_kg',
            'gvw_kg', 'max_ac_charge_kw', 'max_dc_charge_kw',
            'charging_notes', 'list_price', 'price_currency', 'price_basis',
            'price_as_of', 'source_title', 'source_publisher', 'source_url',
            'source_published_on', 'retrieved_at', 'notes',
            'verification_status', 'verified_by_name', 'verified_at',
            'consumption', 'missing_specs', 'assessable', 'updated_at',
        ]
        read_only_fields = fields

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['label'] = instance.label()
        return data

    def get_consumption(self, obj) -> dict | None:
        value, basis = obj.consumption()
        if value is None:
            return None
        return {'kwh_per_km': float(value), 'basis': basis}

    def get_missing_specs(self, obj) -> list:
        return obj.missing_specs()

    def get_assessable(self, obj) -> bool:
        return not obj.missing_specs() and obj.verification_status != 'rejected'
