from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.generics import RetrieveUpdateAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from users.models import User
from users.serializers import UserSerializer, UserUpdateSerializer


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Get current user",
        description="Returns the profile of the authenticated user.",
        responses={200: UserSerializer},
    )
    def get(self, request):
        serializer = UserSerializer(request.user)
        return Response(serializer.data)


@extend_schema_view(
    get=extend_schema(
        summary="Get user by ID",
        description="Returns a user record. Only the authenticated user can access their own record.",
        responses={200: UserSerializer, 403: None, 404: None},
    ),
    patch=extend_schema(
        summary="Update current user",
        description=(
            "Updates the authenticated user's name and mobile_number."
        ),
        request=UserUpdateSerializer,
        responses={200: UserSerializer, 403: None, 404: None},
    ),
    put=extend_schema(exclude=True),
)
class UserDetailView(RetrieveUpdateAPIView):
    permission_classes = [IsAuthenticated]
    queryset = User.objects.all()

    def get_serializer_class(self):
        if self.request.method in ('PUT', 'PATCH'):
            return UserUpdateSerializer
        return UserSerializer

    def get_object(self):
        obj = super().get_object()
        if obj.pk != self.request.user.pk:
            raise PermissionDenied('You can only access your own record')
        return obj

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', True)
        instance = self.get_object()
        serializer = self.get_serializer(
            instance, data=request.data, partial=partial
        )
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        data = UserSerializer(instance).data
        return Response(data, status=status.HTTP_200_OK)
