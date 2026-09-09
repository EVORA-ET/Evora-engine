from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.generics import RetrieveUpdateAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from organizations.models import Organization
from organizations.serializers import (
    OrganizationSerializer,
    OrganizationUpdateSerializer,
)


class OrganizationCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Create organization",
        description="Creates a new organization. The creating user is "
        "linked to it. Rejected with 400 if the user already "
        "belongs to an organization.",
        request=OrganizationSerializer,
        responses={201: OrganizationSerializer},
    )
    def post(self, request):
        if request.user.organization_id is not None:
            return Response(
                {
                    'organization': [
                        'User already belongs to an organization'
                    ]
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = OrganizationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        organization = serializer.save()
        user = request.user
        user.organization = organization
        user.save(update_fields=['organization', 'updated_at'])
        return Response(serializer.data, status=status.HTTP_201_CREATED)


@extend_schema_view(
    get=extend_schema(
        summary="Get organization by ID",
        description="Returns an organization by its ID.",
        responses={200: OrganizationSerializer, 404: None},
    ),
    patch=extend_schema(
        summary="Update organization",
        description="Partially updates an organization.",
        request=OrganizationUpdateSerializer,
        responses={200: OrganizationSerializer, 404: None},
    ),
    put=extend_schema(exclude=True),
)
class OrganizationDetailView(RetrieveUpdateAPIView):
    permission_classes = [IsAuthenticated]
    queryset = Organization.objects.all()

    def get_serializer_class(self):
        if self.request.method in ('PUT', 'PATCH'):
            return OrganizationUpdateSerializer
        return OrganizationSerializer

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', True)
        instance = self.get_object()
        serializer = self.get_serializer(
            instance, data=request.data, partial=partial
        )
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        data = OrganizationSerializer(instance).data
        return Response(data, status=status.HTTP_200_OK)
