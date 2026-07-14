from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.generics import RetrieveUpdateDestroyAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from job_templates.models import JobTemplate
from job_templates.serializers import (
    JobTemplateSerializer,
    JobTemplateUpdateSerializer,
)


def get_user_org(user):
    org = getattr(user, 'organization', None)
    if org is None:
        raise PermissionDenied('User not associated with any organization')
    return org


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
        templates = JobTemplate.objects.filter(
            organization=org
        ).order_by('-created_at')
        serializer = JobTemplateSerializer(templates, many=True)
        return Response(serializer.data)

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
        serializer = JobTemplateSerializer(data=request.data)
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
        return JobTemplate.objects.filter(organization=org)

    def perform_destroy(self, instance):
        instance.delete()

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', True)
        instance = self.get_object()
        serializer = self.get_serializer(
            instance, data=request.data, partial=partial
        )
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        data = JobTemplateSerializer(instance).data
        return Response(data, status=status.HTTP_200_OK)
