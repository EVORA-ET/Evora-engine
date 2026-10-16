from django.db import transaction
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.api import (
    Conflict,
    apply_ordering,
    conflict_response,
    get_uuid,
    get_user_org,
    list_response,
)
from evidence.errors import CalculationError
from evidence.serializers import AssessmentSerializer
from ev_migration import planning, services
from ev_migration.catalogue.service import candidate_for
from ev_migration.overview import (
    ICE_FUELS, fleet_overview, in_service, recorded_costs,
    vehicle_readiness_states)
from ev_migration.models import (
    EVCandidate,
    EVModel,
    MigrationDecision,
    MigrationPlan,
    MigrationPlanItem,
    PlanItemStatusChange,
)
from ev_migration.serializers import (
    AssessmentRequestSerializer,
    EVCandidateSerializer,
    EVModelSerializer,
    MigrationDecisionSerializer,
    MigrationPlanItemSerializer,
    MigrationPlanSerializer,
    PlanItemStatusSerializer,
)


class CandidateListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary='List EV candidates',
                   responses={200: EVCandidateSerializer(many=True)})
    def get(self, request):
        org = get_user_org(request.user)
        qs = EVCandidate.objects.filter(organization=org).select_related(
            'source')
        vt = request.query_params.get('vehicle_type')
        if vt:
            qs = qs.filter(vehicle_type=vt)
        qs = apply_ordering(qs, request, {'manufacturer': 'manufacturer',
                                          'model': 'model',
                                          'created_at': 'created_at'},
                            ['manufacturer', 'model'])
        return list_response(request, qs, EVCandidateSerializer)

    @extend_schema(summary='Record an EV candidate specification',
                   request=EVCandidateSerializer,
                   responses={201: EVCandidateSerializer})
    def post(self, request):
        org = get_user_org(request.user)
        ser = EVCandidateSerializer(data=request.data,
                                    context={'request': request})
        ser.is_valid(raise_exception=True)
        c = ser.save(organization=org, created_by=request.user)
        return Response(EVCandidateSerializer(c).data,
                        status=status.HTTP_201_CREATED)


class CandidateDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary='Get EV candidate',
                   responses={200: EVCandidateSerializer})
    def get(self, request, pk):
        org = get_user_org(request.user)
        return Response(EVCandidateSerializer(
            get_object_or_404(EVCandidate, organization=org, pk=pk)).data)


def _run(request, runner):
    org = get_user_org(request.user)
    ser = AssessmentRequestSerializer(data=request.data,
                                      context={'request': request})
    ser.is_valid(raise_exception=True)
    try:
        assessment = runner(org=org, user=request.user,
                            vehicle=ser.validated_data['vehicle'],
                            candidate=ser.validated_data['ev_candidate'],
                            body=request.data)
    except CalculationError as exc:
        raise ValidationError({exc.field: exc.message})
    return Response(AssessmentSerializer(assessment).data,
                    status=status.HTTP_201_CREATED)


_COMMON = (
    'Body: vehicle_id, ev_candidate_id, optional usage_window_days (30-1095, '
    'default 365) and optional `inputs` {name: {"observation_id"} | {"value", '
    '"unit", "origin": "organization_provided"|"assumed", "note"}} to '
    'override automatic resolution. Every resolved input, its source and '
    'evidence class are stored in the immutable assessment.')


class ReadinessAssessmentView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary='Run an EV readiness screening (mandatory gates)',
        description=_COMMON + ' Also: reserve_fraction (0-1), '
        'charging_connector ("ac"|"dc"), design_day_distance ({"basis": '
        '"measured_p90"|"measured_max"|"planned_max"} or inline value).',
        request=None, responses={201: AssessmentSerializer})
    def post(self, request):
        return _run(request, services.run_readiness)


class TcoAssessmentView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary='Run a TCO comparison (ICE baseline vs EV candidate)',
        description=_COMMON + ' Also: horizon_years (1-30), discount_rate '
        '(fraction), discount_rate_basis (real|nominal, default real), '
        'baseline ("retain_existing"|"replace_with_ice"), optional '
        'sensitivity_fraction.',
        request=None, responses={201: AssessmentSerializer})
    def post(self, request):
        return _run(request, services.run_tco)


class EmissionsAssessmentView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary='Run an operational emissions comparison',
        description=_COMMON + ' Also: fuel_boundary (tank_to_wheel|'
        'well_to_wheel), grid_boundary (generation|'
        'consumption_incl_td_losses).',
        request=None, responses={201: AssessmentSerializer})
    def post(self, request):
        return _run(request, services.run_emissions)


class DecisionListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary='List migration decisions',
                   responses={200: MigrationDecisionSerializer(many=True)})
    def get(self, request):
        org = get_user_org(request.user)
        qs = MigrationDecision.objects.filter(organization=org)
        vid = get_uuid(request, 'vehicle_id')
        if vid:
            qs = qs.filter(vehicle_id=vid)
        qs = apply_ordering(qs, request, {'decided_at': 'decided_at'},
                            ['-decided_at'])
        return list_response(request, qs, MigrationDecisionSerializer)

    @extend_schema(
        summary='Record a migration decision',
        description='Links the decision to the immutable assessments that '
        'informed it. Approval requires a readiness assessment in which '
        'every mandatory check passed.',
        request=MigrationDecisionSerializer,
        responses={201: MigrationDecisionSerializer})
    def post(self, request):
        org = get_user_org(request.user)
        ser = MigrationDecisionSerializer(data=request.data,
                                          context={'request': request})
        ser.is_valid(raise_exception=True)
        vehicle = ser.validated_data['vehicle']
        decision = ser.save(
            organization=org, decided_by=request.user,
            vehicle_snapshot=services._vehicle_identity(vehicle))
        return Response(MigrationDecisionSerializer(decision).data,
                        status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# Migration planning
# ---------------------------------------------------------------------------


def _items_qs(org):
    return (MigrationPlanItem.objects
            .filter(plan__organization=org)
            .select_related('plan', 'vehicle', 'ev_candidate',
                            'readiness_assessment', 'tco_assessment',
                            'decision')
            .prefetch_related('status_history'))


class PlanListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary='List migration plans',
                   description='Filter: status (draft|active|archived).',
                   responses={200: MigrationPlanSerializer(many=True)})
    def get(self, request):
        org = get_user_org(request.user)
        qs = MigrationPlan.objects.filter(organization=org).prefetch_related(
            'items')
        st = request.query_params.get('status')
        if st:
            qs = qs.filter(status=st)
        qs = apply_ordering(qs, request, {'created_at': 'created_at',
                                          'name': 'name'}, ['-created_at'])
        return list_response(request, qs, MigrationPlanSerializer)

    @extend_schema(summary='Create a migration plan',
                   request=MigrationPlanSerializer,
                   responses={201: MigrationPlanSerializer})
    def post(self, request):
        org = get_user_org(request.user)
        ser = MigrationPlanSerializer(data=request.data,
                                      context={'request': request})
        ser.is_valid(raise_exception=True)
        plan = ser.save(organization=org, created_by=request.user,
                        currency=org.currency)
        return Response(MigrationPlanSerializer(plan).data,
                        status=status.HTTP_201_CREATED)


class PlanDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _plan(self, request, pk):
        org = get_user_org(request.user)
        return get_object_or_404(MigrationPlan, organization=org, pk=pk)

    @extend_schema(summary='Get a plan with its items',
                   responses={200: OpenApiTypes.OBJECT})
    def get(self, request, pk):
        plan = self._plan(request, pk)
        org = get_user_org(request.user)
        items = _items_qs(org).filter(plan=plan)
        return Response({
            **MigrationPlanSerializer(plan).data,
            'items': MigrationPlanItemSerializer(
                items, many=True, context={'request': request}).data,
        })

    @extend_schema(summary='Update a plan',
                   request=MigrationPlanSerializer,
                   responses={200: MigrationPlanSerializer})
    def patch(self, request, pk):
        plan = self._plan(request, pk)
        ser = MigrationPlanSerializer(plan, data=request.data, partial=True,
                                      context={'request': request})
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(MigrationPlanSerializer(plan).data)


class PlanItemCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary='Add a vehicle to a plan',
                   request=MigrationPlanItemSerializer,
                   responses={201: MigrationPlanItemSerializer})
    def post(self, request, pk):
        org = get_user_org(request.user)
        plan = get_object_or_404(MigrationPlan, organization=org, pk=pk)
        if plan.status == 'archived':
            raise ValidationError('This plan is archived.')
        ser = MigrationPlanItemSerializer(
            data=request.data, context={'request': request, 'plan': plan})
        ser.is_valid(raise_exception=True)
        if plan.items.filter(vehicle=ser.validated_data['vehicle']).exists():
            raise ValidationError(
                {'vehicle_id': 'This vehicle is already in the plan.'})
        with transaction.atomic():
            item = ser.save(plan=plan, created_by=request.user)
            PlanItemStatusChange.objects.create(
                item=item, from_status='', to_status=item.status,
                note='Added to plan', changed_by=request.user)
        item = _items_qs(org).get(pk=item.pk)
        return Response(MigrationPlanItemSerializer(
            item, context={'request': request}).data,
            status=status.HTTP_201_CREATED)


class PlanItemListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary='List plan items across plans',
                   description='Filters: plan_id, vehicle_id, status.',
                   responses={200: MigrationPlanItemSerializer(many=True)})
    def get(self, request):
        org = get_user_org(request.user)
        qs = _items_qs(org)
        for p, col in (('plan_id', 'plan_id'), ('vehicle_id', 'vehicle_id')):
            value = get_uuid(request, p)
            if value:
                qs = qs.filter(**{col: value})
        st = request.query_params.get('status')
        if st:
            qs = qs.filter(status__in=st.split(','))
        qs = apply_ordering(qs, request, {'phase': 'phase',
                                          'target_date': 'target_date',
                                          'updated_at': 'updated_at'},
                            ['phase', 'target_date'])
        return list_response(request, qs, MigrationPlanItemSerializer)


class PlanItemDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary='Edit a plan item',
                   request=MigrationPlanItemSerializer,
                   responses={200: MigrationPlanItemSerializer})
    def patch(self, request, pk):
        org = get_user_org(request.user)
        item = get_object_or_404(_items_qs(org), pk=pk)
        ser = MigrationPlanItemSerializer(
            item, data=request.data, partial=True,
            context={'request': request, 'plan': item.plan})
        ser.is_valid(raise_exception=True)
        ser.save()
        item = _items_qs(org).get(pk=item.pk)
        return Response(MigrationPlanItemSerializer(
            item, context={'request': request}).data)

    @extend_schema(summary='Remove a plan item',
                   description='Only items still "planned" and never '
                   'started can be removed; otherwise cancel them.',
                   responses={204: None, 409: None})
    def delete(self, request, pk):
        org = get_user_org(request.user)
        item = get_object_or_404(_items_qs(org), pk=pk)
        if item.status != 'planned' or item.status_history.exclude(
                from_status='').exists():
            return conflict_response(Conflict(
                'Only items that are still planned and have never changed '
                'status can be removed. Cancel this item instead so its '
                'history is kept.', {'status': item.status}))
        item.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class PlanItemStatusView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary='Change a plan item status',
        description='planned->in_progress needs a linked approve_transition '
        'decision; completed needs completed_on and a note. Every change is '
        'recorded.', request=PlanItemStatusSerializer,
        responses={200: MigrationPlanItemSerializer})
    def post(self, request, pk):
        org = get_user_org(request.user)
        ser = PlanItemStatusSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        planning.change_status(item_id=pk, org=org, user=request.user,
                               to_status=d['status'], note=d['note'],
                               completed_on=d.get('completed_on'))
        item = _items_qs(org).get(pk=pk)
        return Response(MigrationPlanItemSerializer(
            item, context={'request': request}).data)


class CatalogueListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary='EVORA EV catalogue',
        description='Shared catalogue of EV models with cited manufacturer '
        'documents. Filters: vehicle_type (comma list). Each entry states '
        'which specifications an assessment needs but the document lacks.',
        responses={200: EVModelSerializer(many=True)})
    def get(self, request):
        get_user_org(request.user)
        qs = EVModel.objects.filter(active=True).exclude(
            verification_status='rejected')
        types = [t for t in (request.query_params.get('vehicle_type') or '')
                 .split(',') if t]
        if types:
            qs = qs.filter(vehicle_type__in=types)
        return Response(EVModelSerializer(qs, many=True).data)


class CatalogueCandidateView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary='Use a catalogue model in this organization',
        description='Returns the organization\'s candidate record for the '
        'catalogue model, creating it (with the cited source and any '
        'published list price) when needed. Customers never enter specs.',
        request=None, responses={200: EVCandidateSerializer})
    def post(self, request, pk):
        org = get_user_org(request.user)
        m = get_object_or_404(EVModel, pk=pk, active=True)
        c = candidate_for(org, request.user, m)
        return Response(EVCandidateSerializer(c).data)


class OverviewView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary='EV migration overview counts',
                   responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        return Response(fleet_overview(get_user_org(request.user)))


class VehicleReadinessStatesView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary='Readiness state per ICE vehicle',
                   responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        org = get_user_org(request.user)
        ids = list(in_service(org).filter(fuel_type__in=ICE_FUELS)
                   .values_list('id', flat=True))
        states = vehicle_readiness_states(org, ids)
        return Response({str(k): {**v, 'last_assessed_at': v[
            'last_assessed_at'].isoformat() if v['last_assessed_at'] else None}
            for k, v in states.items()})


class RecordedCostsView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary='Recorded operating costs per month',
        description='months (1-36, default 12), powertrain (ice|ev). Sums '
        'only records that carry a cost; missing costs are counted.',
        responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        org = get_user_org(request.user)
        try:
            months = int(request.query_params.get('months', 12))
        except ValueError:
            raise ValidationError({'months': 'Whole number 1-36.'})
        if not 1 <= months <= 36:
            raise ValidationError({'months': 'Whole number 1-36.'})
        pt = request.query_params.get('powertrain') or None
        if pt not in (None, 'ice', 'ev'):
            raise ValidationError({'powertrain': 'Use ice or ev.'})
        return Response(recorded_costs(org, months, pt))
