from rest_framework import serializers

from users.models import User


class UserSerializer(serializers.ModelSerializer):
    organization_id = serializers.PrimaryKeyRelatedField(
        source='organization',
        read_only=True,
    )

    class Meta:
        model = User
        fields = [
            'id',
            'organization_id',
            'name',
            'email',
            'mobile_number',
            'status',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'email', 'created_at', 'updated_at']


class UserUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['name', 'mobile_number']
