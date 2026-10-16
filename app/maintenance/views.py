from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.api import apply_ordering, get_uuid, get_user_org, list_response
from evidence.serializers import AssessmentSerializer, VoidSerializer
from maintenance import services
from maintenance.models import MaintenanceEvent, ServiceRequirement
from maintenance.serializers import (
    MaintenanceEventSerializer,
    RetireSerializer,
    ServiceRequirementSerializer,
)
from vehicles.models import Vehicle


class RequirementListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary='List service requirements',
                   description='Filters: vehicle_id, include_retired=true.',
                   responses={200: ServiceRequirementSerializer(many=True)})
    def get(self, request):
        org = get_user_org(request.user)
        qs = ServiceRequirement.objects.filter(
            organization=org).select_related('source', 'vehicle')
        if request.query_params.get('include_retired') != 'true':
            qs = qs.filter(retired_at__isnull=True)
        vid = get_uuid(request, 'vehicle_id')
        if vid:
            qs = qs.filter(vehicle_id=vid)
        qs = apply_ordering(qs, request, {'name': 'name'}, ['name'])
        return list_response(request, qs, ServiceRequirementSerializer)

    @extend_schema(summary='Record a documented service requirement',
                   request=ServiceRequirementSerializer,
                   responses={201: ServiceRequirementSerializer})
    def post(self, request):
        org = get_user_org(request.user)
        ser = ServiceRequirementSerializer(data=request.data,
                                           context={'request': request})
        ser.is_valid(raise_exception=True)
        r = ser.save(organization=org, created_by=request.user)
        return Response(ServiceRequirementSerializer(r).data,
                        status=status.HTTP_201_CREATED)


class RequirementRetireView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary='Retire a service requirement (kept for history)',
                   request=RetireSerializer,
                   responses={200: ServiceRequirementSerializer})
    def post(self, request, pk):
        org = get_user_org(request.user)
        r = get_object_or_404(ServiceRequirement, organization=org, pk=pk)
        if r.retired_at:
            raise ValidationError('Already retired.')
        ser = RetireSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        r.retired_at = timezone.now()
        r.retired_reason = ser.validated_data['reason']
        r.save(update_fields=['retired_at', 'retired_reason'])
        return Response(ServiceRequirementSerializer(r).data)


class EventListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary='List maintenance events',
                   description='Filters: vehicle_id, event_type, '
                   'include_voided=true.',
                   responses={200: MaintenanceEventSerializer(many=True)})
    def get(self, request):
        org = get_user_org(request.user)
        qs = MaintenanceEvent.objects.filter(
            organization=org).select_related('vehicle')
        if request.query_params.get('include_voided') != 'true':
            qs = qs.filter(voided_at__isnull=True)
        vid = get_uuid(request, 'vehicle_id')
        if vid:
            qs = qs.filter(vehicle_id=vid)
        et = request.query_params.get('event_type')
        if et:
            qs = qs.filter(event_type=et)
        qs = apply_ordering(qs, request, {'occurred_on': 'occurred_on'},
                            ['-occurred_on'])
        return list_response(request, qs, MaintenanceEventSerializer)

    @extend_schema(summary='Record a maintenance event',
                   request=MaintenanceEventSerializer,
                   responses={201: MaintenanceEventSerializer})
    def post(self, request):
        org = get_user_org(request.user)
        ser = MaintenanceEventSerializer(data=request.data,
                                         context={'request': request})
        ser.is_valid(raise_exception=True)
        ev = ser.save(organization=org, created_by=request.user)
        return Response(MaintenanceEventSerializer(ev).data,
                        status=status.HTTP_201_CREATED)


class EventVoidView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary='Void a maintenance event (kept, not deleted)',
                   request=VoidSerializer,
                   responses={200: MaintenanceEventSerializer})
    def post(self, request, pk):
        org = get_user_org(request.user)
        ev = get_object_or_404(MaintenanceEvent, organization=org, pk=pk)
        if ev.voided_at:
            raise ValidationError('Already voided.')
        ser = VoidSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        ev.voided_at = timezone.now()
        ev.void_reason = ser.validated_data['reason']
        ev.voided_by = request.user
        ev.save(update_fields=['voided_at', 'void_reason', 'voided_by'])
        return Response(MaintenanceEventSerializer(ev).data)


class VehicleMaintenanceStatusView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary='Rule-based maintenance status for a vehicle (live)',
        description='Service due/overdue from documented intervals, open '
        'faults, battery checks from measured SoH/capacity, and a summary of '
        'recorded events over `window_days` (default 365). Not a prediction. '
        'Not persisted; POST /maintenance/assessments to keep a snapshot.',
        responses={200: OpenApiTypes.OBJECT})
    def get(self, request, pk):
        org = get_user_org(request.user)
        vehicle = get_object_or_404(Vehicle, organization=org, pk=pk)
        window = services.parse_window(
            request.query_params.get('window_days'))
        result, snapshot, params = services.status_for(org, vehicle, window)
        return Response({'vehicle_id': str(vehicle.pk), 'parameters': params,
                         'evidence': snapshot['inputs'], 'result': result})


class FleetMaintenanceStatusView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary='Rule-based maintenance status across the fleet (live)',
        description='Runs the same rules as the per-vehicle status for every '
        'vehicle with maintenance records or battery readings; others are '
        'counted as having no records. `window_days` (default 365) applies '
        'to the record totals. Not persisted, not a prediction.',
        responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        org = get_user_org(request.user)
        window = services.parse_window(
            request.query_params.get('window_days'))
        return Response(services.fleet_status(org, window))


class MaintenanceAssessmentCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary='Persist a maintenance status assessment',
                   description='Body: vehicle_id, optional window_days.',
                   request=None, responses={201: AssessmentSerializer})
    def post(self, request):
        org = get_user_org(request.user)
        vid = request.data.get('vehicle_id')
        vehicle = Vehicle.objects.filter(organization=org, pk=vid).first() \
            if vid else None
        if vehicle is None:
            raise ValidationError({'vehicle_id': 'Vehicle not found.'})
        window = services.parse_window(request.data.get('window_days'))
        a = services.persist_status(org, request.user, vehicle, window)
        return Response(AssessmentSerializer(a).data,
                        status=status.HTTP_201_CREATED)
