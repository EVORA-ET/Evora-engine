from rest_framework import serializers
from rest_framework_gis.fields import GeometryField

from common.fields import OrgScopedPrimaryKeyField, request_org
from depots.models import Depot
from fleets.models import Fleet
from job_instances.services import summarise_current_job
from vehicles.models import Vehicle, BulkUpload


class RegistrationUniquenessMixin:
    """Registration numbers must be unique per organization (case-insensitive).

    The column has no database unique constraint and older data may already
    contain duplicates, so this guards new writes without a risky migration.
    """

    def validate_registration_number(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('This field may not be blank.')
        org = request_org(self)
        if org is not None:
            clash = Vehicle.objects.filter(
                organization=org, registration_number__iexact=value)
            if self.instance is not None:
                clash = clash.exclude(pk=self.instance.pk)
            if clash.exists():
                raise serializers.ValidationError(
                    'A vehicle with this registration number already exists.')
        return value


class VehicleSerializer(RegistrationUniquenessMixin, serializers.ModelSerializer):
    current_location = GeometryField(required=False, allow_null=True)
    organization_id = serializers.UUIDField(read_only=True)
    fleet_id = OrgScopedPrimaryKeyField(
        queryset=Fleet.objects.all(), source='fleet',
        required=False, allow_null=True)
    depot_id = OrgScopedPrimaryKeyField(
        queryset=Depot.objects.all(), source='depot',
        required=False, allow_null=True)

    class Meta:
        model = Vehicle
        fields = [
            'id',
            'organization_id',
            'registration_number',
            'vin',
            'chassis_number',
            'manufacturer',
            'model',
            'variant',
            'manufacture_year',
            'purchase_date',
            'vehicle_type',
            'fuel_type',
            'engine_cc',
            'fuel_tank_capacity',
            'battery_capacity_kwh',
            'mileage_kmpl',
            'payload_kg',
            'seating_capacity',
            'gvw_kg',
            'status',
            'current_location',
            'assigned_driver_id',
            'fleet_id',
            'depot_id',
            'odometer_km',
            'avg_daily_km',
            'avg_monthly_km',
            'trips_per_day',
            'fuel_cost_per_month',
            'maintenance_cost_per_month',
            'insurance_expiry',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id', 'organization_id', 'created_at', 'updated_at',
        ]


class VehicleOpsSerializer(VehicleSerializer):
    """Vehicle plus display names and current assignment, for Operations.

    `open_job_count` / `current_job` appear only when the view supplies
    `open_jobs` in the context, so the Dashboard's plain list is unchanged.
    """

    fleet_name = serializers.CharField(source='fleet.name', read_only=True, default=None)
    depot_name = serializers.CharField(source='depot.name', read_only=True, default=None)

    class Meta(VehicleSerializer.Meta):
        fields = VehicleSerializer.Meta.fields + ['fleet_name', 'depot_name']

    def to_representation(self, instance):
        data = super().to_representation(instance)
        open_jobs = self.context.get('open_jobs')
        if open_jobs is not None:
            jobs = open_jobs.get(instance.pk, [])
            data['open_job_count'] = len(jobs)
            data['current_job'] = summarise_current_job(jobs)
        return data


class VehicleUpdateSerializer(RegistrationUniquenessMixin, serializers.ModelSerializer):
    current_location = GeometryField(required=False, allow_null=True)
    fleet_id = OrgScopedPrimaryKeyField(
        queryset=Fleet.objects.all(), source='fleet',
        required=False, allow_null=True)
    depot_id = OrgScopedPrimaryKeyField(
        queryset=Depot.objects.all(), source='depot',
        required=False, allow_null=True)

    class Meta:
        model = Vehicle
        fields = [
            'registration_number',
            'vin',
            'chassis_number',
            'manufacturer',
            'model',
            'variant',
            'manufacture_year',
            'purchase_date',
            'vehicle_type',
            'fuel_type',
            'engine_cc',
            'fuel_tank_capacity',
            'battery_capacity_kwh',
            'mileage_kmpl',
            'payload_kg',
            'seating_capacity',
            'gvw_kg',
            'status',
            'current_location',
            'assigned_driver_id',
            'fleet_id',
            'depot_id',
            'odometer_km',
            'avg_daily_km',
            'avg_monthly_km',
            'trips_per_day',
            'fuel_cost_per_month',
            'maintenance_cost_per_month',
            'insurance_expiry',
        ]


class BulkUploadSerializer(serializers.ModelSerializer):
    class Meta:
        model = BulkUpload
        fields = [
            'id',
            'file',
            'status',
            'total_rows',
            'processed_rows',
            'failed_rows',
            'error_report',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id', 'status', 'total_rows', 'processed_rows',
            'failed_rows', 'error_report', 'created_at', 'updated_at',
        ]


class BulkUploadStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = BulkUpload
        fields = [
            'id',
            'status',
            'total_rows',
            'processed_rows',
            'failed_rows',
            'error_report',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields
