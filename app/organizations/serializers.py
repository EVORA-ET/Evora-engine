from rest_framework import serializers
from rest_framework_gis.fields import GeometryField

from organizations.models import Organization


class OrganizationSerializer(serializers.ModelSerializer):
    headquarters_location = GeometryField(
        required=False, allow_null=True
    )

    class Meta:
        model = Organization
        fields = [
            'id',
            'name',
            'industry',
            'country',
            'headquarters_address',
            'headquarters_location',
            'operating_states',
            'operating_cities',
            'gst_number',
            'website',
            'logo_url',
            'timezone',
            'currency',
            'business_hours',
            'data_quality_score',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class OrganizationUpdateSerializer(serializers.ModelSerializer):
    headquarters_location = GeometryField(
        required=False, allow_null=True
    )

    class Meta:
        model = Organization
        fields = [
            'name',
            'industry',
            'country',
            'headquarters_address',
            'headquarters_location',
            'operating_states',
            'operating_cities',
            'gst_number',
            'website',
            'logo_url',
            'timezone',
            'currency',
            'business_hours',
            'data_quality_score',
        ]
