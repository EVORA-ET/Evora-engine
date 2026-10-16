from django.db.models import Q
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.generics import RetrieveUpdateDestroyAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.api import (
    Conflict,
    apply_ordering,
    conflict_response,
    get_choice_list,
    get_search,
    get_user_org,
    get_uuid,
    list_response,
    requested_window,
    resolve_timezone,
)
from job_instances import services
from job_instances.models import JobInstance, JobInstanceStatus
from job_instances.serializers import (
    AssignSerializer,
    JobInstanceCreateSerializer,
    JobInstanceDetailSerializer,
    JobInstanceSerializer,
    JobInstanceUpdateSerializer,
    StatusSerializer,
)

ASSIGNMENT_FILTERS = (
    'unassigned',
    'missing_vehicle',
    'missing_driver',
    'assigned',
)
ORDERING = {
    'scheduled_start_at': 'scheduled_start_at',
    'scheduled_end_at': 'scheduled_end_at',
    'created_at': 'created_at',
    'status': 'status',
    'route': 'job_template__name',
    'vehicle': 'vehicle__registration_number',
    'driver': 'driver__name',
}


def base_queryset(org):
    return JobInstance.objects.filter(organization=org).select_related(
        'job_template__source_depot',
        'job_template__destination_depot',
        'vehicle',
        'driver',
    )


def filter_jobs(request, org, queryset):
    start, end, _ = requested_window(request, org)
    if start is not None:
        queryset = queryset.filter(scheduled_start_at__gte=start)
    if end is not None:
        queryset = queryset.filter(scheduled_start_at__lt=end)

    statuses = get_choice_list(request, 'status', JobInstanceStatus.values)
    if statuses:
        queryset = queryset.filter(status__in=statuses)

    for param, column in (
        ('job_template_id', 'job_template_id'),
        ('vehicle_id', 'vehicle_id'),
        ('driver_id', 'driver_id'),
    ):
        value = get_uuid(request, param)
        if value:
            queryset = queryset.filter(**{column: value})

    depot = get_uuid(request, 'depot_id')
    if depot:
        queryset = queryset.filter(
            Q(job_template__source_depot_id=depot)
            | Q(job_template__destination_depot_id=depot)
        )

    assignment = request.query_params.get('assignment')
    if assignment:
        if assignment not in ASSIGNMENT_FILTERS:
            raise ValidationError(
                {'assignment': f'Use one of: {", ".join(ASSIGNMENT_FILTERS)}.'}
            )
        queryset = {
            'unassigned': queryset.filter(
                vehicle__isnull=True, driver__isnull=True
            ),
            'missing_vehicle': queryset.filter(vehicle__isnull=True),
            'missing_driver': queryset.filter(driver__isnull=True),
            'assigned': queryset.filter(
                vehicle__isnull=False, driver__isnull=False
            ),
        }[assignment]

    search = get_search(request)
    if search:
        queryset = queryset.filter(
            Q(job_template__name__icontains=search)
            | Q(vehicle__registration_number__icontains=search)
            | Q(driver__name__icontains=search)
            | Q(driver__driver_code__icontains=search)
            | Q(job_template__source_depot__name__icontains=search)
            | Q(job_template__destination_depot__name__icontains=search)
        )
    return queryset


class JobInstanceListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary='List job instances',
        description=(
            "Returns job instances for the authenticated user's organization. "
            'Without query parameters this is a bare array (legacy). With '
            '`page`/`page_size` it returns `{count, page, page_size, '
            'total_pages, results}`. Filters: `date` or `date_from`/`date_to` '
            '(YYYY-MM-DD, interpreted in `tz`), `status` (comma list), '
            '`job_template_id`, `vehicle_id`, `driver_id`, `depot_id`, '
            '`assignment` (unassigned|missing_vehicle|missing_driver|assigned), '
            '`search`, `ordering`.'
        ),
        responses={200: JobInstanceSerializer(many=True)},
    )
    def get(self, request):
        org = get_user_org(request.user)
        queryset = filter_jobs(request, org, base_queryset(org))
        queryset = apply_ordering(queryset, request, ORDERING, ['-created_at'])
        return list_response(request, queryset, JobInstanceSerializer)

    @extend_schema(
        summary='Create job instance',
        description=(
            'Schedules a job from a route (job template). A new job is PENDING, '
            'or ASSIGNED if both a vehicle and a driver are supplied and pass '
            'validation. `scheduled_end_at` defaults to start + the route\'s '
            'estimated duration when omitted.'
        ),
        request=JobInstanceCreateSerializer,
        responses={201: JobInstanceSerializer},
    )
    def post(self, request):
        org = get_user_org(request.user)
        serializer = JobInstanceCreateSerializer(
            data=request.data, context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        tz, _ = resolve_timezone(request, org)
        job = services.create_job(
            org=org,
            tz=tz,
            template=data['job_template'],
            start=data['scheduled_start_at'],
            end=data.get('scheduled_end_at'),
            vehicle_id=data['vehicle'].pk if data.get('vehicle') else None,
            driver_id=data['driver'].pk if data.get('driver') else None,
        )
        job = base_queryset(org).get(pk=job.pk)
        return Response(
            JobInstanceSerializer(job).data, status=status.HTTP_201_CREATED
        )


@extend_schema_view(
    get=extend_schema(
        summary='Get job instance by ID',
        description='Returns a job with its route summary and stops.',
        responses={200: JobInstanceDetailSerializer, 404: None},
    ),
    patch=extend_schema(
        summary='Update job instance',
        description=(
            'Edit route, schedule or progress. Vehicle/driver/status cannot be '
            'changed here: use `/assign` and `/status`.'
        ),
        request=JobInstanceUpdateSerializer,
        responses={200: JobInstanceSerializer, 404: None},
    ),
    delete=extend_schema(
        summary='Delete job instance',
        description='Blocked (409) for in-progress or completed jobs; cancel instead.',
        responses={204: None, 404: None, 409: None},
    ),
    put=extend_schema(exclude=True),
)
class JobInstanceDetailView(RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'patch', 'delete']

    def get_serializer_class(self):
        if self.request.method == 'PATCH':
            return JobInstanceUpdateSerializer
        return JobInstanceDetailSerializer

    def get_queryset(self):
        org = get_user_org(self.request.user)
        return base_queryset(org).prefetch_related('instance_stops__stop')

    def update(self, request, *args, **kwargs):
        org = get_user_org(request.user)
        job = self.get_object()

        forbidden = {
            'vehicle_id': 'use POST /job-instance/<id>/assign',
            'driver_id': 'use POST /job-instance/<id>/assign',
            'status': 'use POST /job-instance/<id>/status',
            'actual_start_at': 'recorded automatically when the job starts',
            'actual_end_at': 'recorded automatically when the job ends',
        }
        errors = {
            k: [f'Not editable here: {why}.']
            for k, why in forbidden.items()
            if k in request.data
        }
        if errors:
            raise ValidationError(errors)

        serializer = JobInstanceUpdateSerializer(
            data=request.data, partial=True, context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        tz, _ = resolve_timezone(request, org)

        if 'completion_percentage' in data:
            if job.status != JobInstanceStatus.IN_PROGRESS:
                raise ValidationError(
                    {
                        'completion_percentage': [
                            'Progress can only be edited while the job is in progress.'
                        ]
                    }
                )
            if set(data) - {'completion_percentage'}:
                raise ValidationError(
                    'Edit progress separately from the schedule.'
                )
            job.completion_percentage = data['completion_percentage']
            job.save(update_fields=['completion_percentage', 'updated_at'])
        elif data:
            services.update_schedule(
                job_id=job.pk,
                org=org,
                tz=tz,
                start=data.get('scheduled_start_at', services.UNSET),
                end=data.get('scheduled_end_at', services.UNSET),
                template=data.get('job_template', services.UNSET),
            )
        fresh = self.get_queryset().get(pk=job.pk)
        return Response(JobInstanceDetailSerializer(fresh).data)

    def destroy(self, request, *args, **kwargs):
        job = self.get_object()
        if job.status in (
            JobInstanceStatus.IN_PROGRESS,
            JobInstanceStatus.COMPLETED,
        ):
            return conflict_response(
                Conflict(
                    f'A {job.status.lower().replace("_", " ")} job is part of the operating '
                    'history and cannot be deleted. Cancel it instead if it should not run.',
                    {'status': job.status},
                )
            )
        job.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class JobAssignView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary='Assign or reassign vehicle and driver',
        description=(
            'Sets `vehicle_id` and/or `driver_id` (null to unassign; omit to '
            'leave unchanged). Validated server-side inside a transaction with '
            'row locks: resources must belong to the organization, be usable '
            'on the job date, and not overlap another open job. The job '
            'becomes ASSIGNED when both are set, otherwise PENDING.'
        ),
        request=AssignSerializer,
        responses={200: JobInstanceDetailSerializer},
    )
    def post(self, request, pk):
        org = get_user_org(request.user)
        serializer = AssignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        tz, _ = resolve_timezone(request, org)
        data = serializer.validated_data
        services.assign_job(
            job_id=pk,
            org=org,
            tz=tz,
            vehicle_id=(
                data['vehicle_id'] if 'vehicle_id' in data else services.UNSET
            ),
            driver_id=(
                data['driver_id'] if 'driver_id' in data else services.UNSET
            ),
        )
        job = (
            base_queryset(org)
            .prefetch_related('instance_stops__stop')
            .get(pk=pk)
        )
        return Response(JobInstanceDetailSerializer(job).data)


class JobStatusView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary='Change job status',
        description=(
            'Allowed: ASSIGNED->IN_PROGRESS, IN_PROGRESS->COMPLETED, and '
            'PENDING/ASSIGNED/IN_PROGRESS->CANCELLED. Actual start/end times '
            'are recorded automatically.'
        ),
        request=StatusSerializer,
        responses={200: JobInstanceDetailSerializer},
    )
    def post(self, request, pk):
        org = get_user_org(request.user)
        serializer = StatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        tz, _ = resolve_timezone(request, org)
        services.change_status(
            job_id=pk,
            org=org,
            tz=tz,
            new_status=serializer.validated_data['status'],
        )
        job = (
            base_queryset(org)
            .prefetch_related('instance_stops__stop')
            .get(pk=pk)
        )
        return Response(JobInstanceDetailSerializer(job).data)


class JobEligibleResourcesView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary='Vehicles and drivers that can take this job',
        description=(
            'Each candidate is flagged `eligible` with human-readable '
            '`reasons` when not (status, expired insurance/licence, or an '
            'overlapping booking). Eligible candidates sort first.'
        ),
        responses={200: None},
    )
    def get(self, request, pk):
        org = get_user_org(request.user)
        try:
            job = base_queryset(org).get(pk=pk)
        except JobInstance.DoesNotExist:
            raise NotFound('Job not found.')
        tz, _ = resolve_timezone(request, org)
        try:
            limit = min(int(request.query_params.get('limit', 100)), 200)
        except ValueError:
            raise ValidationError({'limit': 'Must be an integer.'})
        payload = services.eligible_resources(
            job=job,
            org=org,
            tz=tz,
            search=get_search(request),
            limit=max(limit, 1),
        )
        return Response(payload)
