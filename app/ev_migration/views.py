from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.api import apply_ordering, get_uuid, get_user_org, list_response
from evidence.errors import CalculationError
from evidence.serializers import AssessmentSerializer
from ev_migration import services
from ev_migration.models import EVCandidate, MigrationDecision
from ev_migration.serializers import (
    AssessmentRequestSerializer,
    EVCandidateSerializer,
    MigrationDecisionSerializer,
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
