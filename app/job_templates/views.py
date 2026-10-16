from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.generics import RetrieveUpdateDestroyAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from django.db.models import Count, Q
from django.utils import timezone

from common.api import (
    Conflict, apply_ordering, conflict_response, get_choice_list, get_search,
    get_uuid, list_response,
)
from job_instances.models import JobInstance
from job_templates.models import JobTemplate, JobTemplateStatus
from operations import rules
from job_templates.serializers import (
    JobTemplateSerializer,
    JobTemplateUpdateSerializer,
)


def get_user_org(user):
    org = getattr(user, 'organization', None)
    if org is None:
        raise PermissionDenied('User not associated with any organization')
    return org


ORDERING = {
    'name': 'name',
    'status': 'status',
    'created_at': 'created_at',
    'source': 'source_depot__name',
    'destination': 'destination_depot__name',
    'estimated_distance_km': 'estimated_distance_km',
    'estimated_duration_minutes': 'estimated_duration_minutes',
}


def template_stats(org, ids):
    now = timezone.now()
    rows = (
        JobInstance.objects.filter(organization=org, job_template_id__in=ids)
        .values('job_template_id')
        .annotate(
            job_count=Count('id'),
            upcoming=Count('id', filter=Q(
                status__in=rules.OPEN_STATUSES, scheduled_start_at__gte=now)),
        )
    )
    stats = {pk: {'job_count': 0, 'upcoming_job_count': 0} for pk in ids}
    for r in rows:
        stats[r['job_template_id']] = {
            'job_count': r['job_count'], 'upcoming_job_count': r['upcoming']}
    return stats


def filter_templates(request, queryset):
    search = get_search(request)
    if search:
        queryset = queryset.filter(
            Q(name__icontains=search) | Q(description__icontains=search)
            | Q(source_depot__name__icontains=search)
            | Q(destination_depot__name__icontains=search)
        )
    statuses = get_choice_list(request, 'status', JobTemplateStatus.values)
    if statuses:
        queryset = queryset.filter(status__in=statuses)
    for param, column in (
        ('source_depot_id', 'source_depot_id'),
        ('destination_depot_id', 'destination_depot_id'),
    ):
        value = get_uuid(request, param)
        if value:
            queryset = queryset.filter(**{column: value})
    depot = get_uuid(request, 'depot_id')
    if depot:
        queryset = queryset.filter(Q(source_depot_id=depot) | Q(destination_depot_id=depot))
    recurring = request.query_params.get('is_recurring')
    if recurring in ('true', 'false'):
        queryset = queryset.filter(is_recurring=recurring == 'true')
    return queryset


class JobTemplateListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary='List job templates',
        description=(
            "Returns all job templates belonging to the "
            "authenticated user's organization."
        ),
        responses={200: JobTemplateSerializer(many=True)},
    )
    def get(self, request):
        org = get_user_org(request.user)
        templates = filter_templates(
            request,
            JobTemplate.objects.filter(organization=org)
            .select_related('source_depot', 'destination_depot'),
        )
        templates = apply_ordering(templates, request, ORDERING, ['-created_at'])
        return list_response(
            request, templates, JobTemplateSerializer,
            enrich=lambda items: {'template_stats': template_stats(org, [t.pk for t in items])},
        )

    @extend_schema(
        summary='Create job template',
        description=(
            "Creates a new job template under the authenticated "
            "user's organization."
        ),
        request=JobTemplateSerializer,
        responses={201: JobTemplateSerializer},
    )
    def post(self, request):
        org = get_user_org(request.user)
        serializer = JobTemplateSerializer(
            data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        serializer.save(organization=org, created_by=request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


@extend_schema_view(
    get=extend_schema(
        summary='Get job template by ID',
        description=(
            "Returns a job template by its ID (scoped to "
            "user's organization)."
        ),
        responses={200: JobTemplateSerializer, 404: None},
    ),
    patch=extend_schema(
        summary='Update job template',
        description=(
            "Partially updates a job template (scoped to "
            "user's organization)."
        ),
        request=JobTemplateUpdateSerializer,
        responses={200: JobTemplateSerializer, 404: None},
    ),
    delete=extend_schema(
        summary='Delete job template',
        description=(
            "Deletes a job template (scoped to "
            "user's organization)."
        ),
        responses={204: None, 404: None},
    ),
    put=extend_schema(exclude=True),
)
class JobTemplateDetailView(RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'patch', 'delete']

    def get_serializer_class(self):
        if self.request.method in ('PUT', 'PATCH'):
            return JobTemplateUpdateSerializer
        return JobTemplateSerializer

    def get_queryset(self):
        org = get_user_org(self.request.user)
        return JobTemplate.objects.filter(organization=org).select_related(
            'source_depot', 'destination_depot')

    def _present(self, template):
        org = get_user_org(self.request.user)
        template = self.get_queryset().get(pk=template.pk)
        return JobTemplateSerializer(
            template, context={'template_stats': template_stats(org, [template.pk])}
        ).data

    def retrieve(self, request, *args, **kwargs):
        return Response(self._present(self.get_object()))

    def destroy(self, request, *args, **kwargs):
        template = self.get_object()
        total = JobInstance.objects.filter(job_template=template).count()
        if total:
            return conflict_response(Conflict(
                f'This route has {total} scheduled job(s). Deleting it would '
                'delete them and their stops. Archive the route instead to '
                'stop new jobs while keeping history.',
                {'job_instances': total},
            ))
        template.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', True)
        instance = self.get_object()
        serializer = self.get_serializer(
            instance, data=request.data, partial=partial
        )
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return Response(self._present(instance), status=status.HTTP_200_OK)
