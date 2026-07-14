from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.generics import RetrieveUpdateDestroyAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from instance_stops.models import InstanceStop
from instance_stops.serializers import (
    InstanceStopSerializer,
    InstanceStopUpdateSerializer,
)


def get_user_org(user):
    org = getattr(user, 'organization', None)
    if org is None:
        raise PermissionDenied('User not associated with any organization')
    return org


class InstanceStopListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary='List instance stops',
        description=(
            "Returns all instance stops belonging to the "
            "authenticated user's organization."
        ),
        responses={200: InstanceStopSerializer(many=True)},
    )
    def get(self, request):
        org = get_user_org(request.user)
        instances = InstanceStop.objects.filter(
            job_instance__organization=org
        ).order_by('sequence_number')
        serializer = InstanceStopSerializer(instances, many=True)
        return Response(serializer.data)

    @extend_schema(
        summary='Create instance stop',
        description=(
            "Creates a new instance stop under the authenticated "
            "user's organization."
        ),
        request=InstanceStopSerializer,
        responses={201: InstanceStopSerializer},
    )
    def post(self, request):
        org = get_user_org(request.user)
        serializer = InstanceStopSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        job_instance = serializer.validated_data.get('job_instance')
        if job_instance and job_instance.organization != org:
            raise PermissionDenied(
                'Job instance does not belong to your organization.'
            )

        stop = serializer.validated_data.get('stop')
        if stop and stop.organization != org:
            raise PermissionDenied(
                'Stop does not belong to your organization.'
            )

        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)


@extend_schema_view(
    get=extend_schema(
        summary='Get instance stop by ID',
        description=(
            "Returns an instance stop by its ID (scoped to "
            "user's organization)."
        ),
        responses={200: InstanceStopSerializer, 404: None},
    ),
    patch=extend_schema(
        summary='Update instance stop',
        description=(
            "Partially updates an instance stop (scoped to "
            "user's organization)."
        ),
        request=InstanceStopUpdateSerializer,
        responses={200: InstanceStopSerializer, 404: None},
    ),
    delete=extend_schema(
        summary='Delete instance stop',
        description=(
            "Deletes an instance stop (scoped to "
            "user's organization)."
        ),
        responses={204: None, 404: None},
    ),
    put=extend_schema(exclude=True),
)
class InstanceStopDetailView(RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'patch', 'delete']

    def get_serializer_class(self):
        if self.request.method in ('PUT', 'PATCH'):
            return InstanceStopUpdateSerializer
        return InstanceStopSerializer

    def get_queryset(self):
        org = get_user_org(self.request.user)
        return InstanceStop.objects.filter(
            job_instance__organization=org
        )

    def perform_destroy(self, instance):
        instance.delete()

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', True)
        instance = self.get_object()
        serializer = self.get_serializer(
            instance, data=request.data, partial=partial
        )
        serializer.is_valid(raise_exception=True)

        org = get_user_org(request.user)
        job_instance = serializer.validated_data.get('job_instance')
        if job_instance and job_instance.organization != org:
            raise PermissionDenied(
                'Job instance does not belong to your organization.'
            )
        stop = serializer.validated_data.get('stop')
        if stop and stop.organization != org:
            raise PermissionDenied(
                'Stop does not belong to your organization.'
            )

        self.perform_update(serializer)
        data = InstanceStopSerializer(instance).data
        return Response(data, status=status.HTTP_200_OK)
