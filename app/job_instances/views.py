from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.generics import RetrieveUpdateDestroyAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from job_instances.models import JobInstance
from job_instances.serializers import (
    JobInstanceSerializer,
    JobInstanceUpdateSerializer,
)


def get_user_org(user):
    org = getattr(user, 'organization', None)
    if org is None:
        raise PermissionDenied('User not associated with any organization')
    return org


class JobInstanceListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary='List job instances',
        description=(
            "Returns all job instances belonging to the "
            "authenticated user's organization."
        ),
        responses={200: JobInstanceSerializer(many=True)},
    )
    def get(self, request):
        org = get_user_org(request.user)
        instances = JobInstance.objects.filter(
            organization=org
        ).order_by('-created_at')
        serializer = JobInstanceSerializer(instances, many=True)
        return Response(serializer.data)

    @extend_schema(
        summary='Create job instance',
        description=(
            "Creates a new job instance under the authenticated "
            "user's organization."
        ),
        request=JobInstanceSerializer,
        responses={201: JobInstanceSerializer},
    )
    def post(self, request):
        org = get_user_org(request.user)
        serializer = JobInstanceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(organization=org)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


@extend_schema_view(
    get=extend_schema(
        summary='Get job instance by ID',
        description=(
            "Returns a job instance by its ID (scoped to "
            "user's organization)."
        ),
        responses={200: JobInstanceSerializer, 404: None},
    ),
    patch=extend_schema(
        summary='Update job instance',
        description=(
            "Partially updates a job instance (scoped to "
            "user's organization)."
        ),
        request=JobInstanceUpdateSerializer,
        responses={200: JobInstanceSerializer, 404: None},
    ),
    delete=extend_schema(
        summary='Delete job instance',
        description=(
            "Deletes a job instance (scoped to "
            "user's organization)."
        ),
        responses={204: None, 404: None},
    ),
    put=extend_schema(exclude=True),
)
class JobInstanceDetailView(RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'patch', 'delete']

    def get_serializer_class(self):
        if self.request.method in ('PUT', 'PATCH'):
            return JobInstanceUpdateSerializer
        return JobInstanceSerializer

    def get_queryset(self):
        org = get_user_org(self.request.user)
        return JobInstance.objects.filter(organization=org)

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
        data = JobInstanceSerializer(instance).data
        return Response(data, status=status.HTTP_200_OK)
