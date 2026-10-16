import csv
import threading

import openpyxl
from django.db.models import Q
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.exceptions import (
    NotFound,
    PermissionDenied,
    ValidationError,
)
from rest_framework.generics import RetrieveUpdateDestroyAPIView
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.api import (
    Conflict, apply_ordering, conflict_response, get_choice_list, get_search,
    get_uuid, list_response, selected_day,
)
from job_instances.models import JobInstance
from job_instances.services import open_jobs_by
from operations import rules
from vehicles.models import (
    BulkUpload, FuelType, UploadStatus, Vehicle, VehicleStatus, VehicleType,
)
from vehicles.serializers import (
    BulkUploadSerializer,
    BulkUploadStatusSerializer,
    VehicleOpsSerializer,
    VehicleSerializer,
    VehicleUpdateSerializer,
)


def get_user_org(user):
    org = getattr(user, 'organization', None)
    if org is None:
        raise PermissionDenied('User not associated with any organization')
    return org


VEHICLE_FIELD_MAP = {
    'registration_number': 'registration_number',
    'vin': 'vin',
    'chassis_number': 'chassis_number',
    'manufacturer': 'manufacturer',
    'model': 'model',
    'variant': 'variant',
    'manufacture_year': 'manufacture_year',
    'purchase_date': 'purchase_date',
    'vehicle_type': 'vehicle_type',
    'fuel_type': 'fuel_type',
    'engine_cc': 'engine_cc',
    'fuel_tank_capacity': 'fuel_tank_capacity',
    'battery_capacity_kwh': 'battery_capacity_kwh',
    'mileage_kmpl': 'mileage_kmpl',
    'payload_kg': 'payload_kg',
    'seating_capacity': 'seating_capacity',
    'gvw_kg': 'gvw_kg',
    'status': 'status',
    'assigned_driver_id': 'assigned_driver_id',
    'odometer_km': 'odometer_km',
    'avg_daily_km': 'avg_daily_km',
    'avg_monthly_km': 'avg_monthly_km',
    'trips_per_day': 'trips_per_day',
    'fuel_cost_per_month': 'fuel_cost_per_month',
    'maintenance_cost_per_month': 'maintenance_cost_per_month',
    'insurance_expiry': 'insurance_expiry',
}


ORDERING = {
    'registration_number': 'registration_number',
    'manufacturer': 'manufacturer',
    'model': 'model',
    'status': 'status',
    'fuel_type': 'fuel_type',
    'vehicle_type': 'vehicle_type',
    'manufacture_year': 'manufacture_year',
    'insurance_expiry': 'insurance_expiry',
    'created_at': 'created_at',
    'fleet': 'fleet__name',
    'depot': 'depot__name',
}
AVAILABILITY = ('available', 'assigned', 'unavailable')


def base_queryset(org):
    return Vehicle.objects.filter(organization=org).select_related('fleet', 'depot')


def _fk_filter(request, queryset, param, column):
    raw = request.query_params.get(param)
    if raw == 'none':
        return queryset.filter(**{f'{column}__isnull': True})
    value = get_uuid(request, param)
    if value:
        return queryset.filter(**{column: value})
    return queryset


def filter_vehicles(request, org, queryset):
    search = get_search(request)
    if search:
        queryset = queryset.filter(
            Q(registration_number__icontains=search)
            | Q(manufacturer__icontains=search)
            | Q(model__icontains=search)
            | Q(vin__icontains=search)
        )

    statuses = get_choice_list(request, 'status', VehicleStatus.values)
    if statuses:
        queryset = queryset.filter(status__in=statuses)
    fuels = get_choice_list(request, 'fuel_type', FuelType.values)
    if fuels:
        queryset = queryset.filter(fuel_type__in=fuels)
    types = get_choice_list(request, 'vehicle_type', VehicleType.values)
    if types:
        queryset = queryset.filter(vehicle_type__in=types)

    powertrain = request.query_params.get('powertrain')
    if powertrain:
        if powertrain not in ('ev', 'ice'):
            raise ValidationError({'powertrain': 'Use ev or ice.'})
        queryset = (
            queryset.filter(fuel_type=FuelType.EV) if powertrain == 'ev'
            else queryset.exclude(fuel_type=FuelType.EV)
        )

    queryset = _fk_filter(request, queryset, 'fleet_id', 'fleet_id')
    queryset = _fk_filter(request, queryset, 'depot_id', 'depot_id')

    availability = request.query_params.get('availability')
    if availability:
        if availability not in AVAILABILITY:
            raise ValidationError({'availability': f'Use one of: {", ".join(AVAILABILITY)}.'})
        _, _, day, start, end = selected_day(request, org)
        usable = rules.vehicle_usable_q(day)
        committed = rules.committed_vehicle_ids(org, start, end)
        if availability == 'unavailable':
            queryset = queryset.exclude(usable)
        elif availability == 'assigned':
            queryset = queryset.filter(usable, pk__in=committed)
        else:
            queryset = queryset.filter(usable).exclude(pk__in=committed)
    return queryset


def ops_context(org, vehicles):
    ids = [v.pk for v in vehicles]
    return {'open_jobs': open_jobs_by('vehicle', ids, org)}


class VehicleListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary='List vehicles',
        description=(
            "Returns vehicles for the authenticated user's organization. "
            'Without query parameters this is a bare array (legacy). With '
            '`page`/`page_size` it returns a page envelope and includes fleet/'
            'depot names and the current job. Filters: `search`, `status`, '
            '`fuel_type`, `vehicle_type` (comma lists), `powertrain` (ev|ice), '
            '`fleet_id`, `depot_id` (UUID or `none`), `availability` '
            '(available|assigned|unavailable, relative to `date`), `ordering`.'
        ),
        responses={200: VehicleOpsSerializer(many=True)},
    )
    def get(self, request):
        org = get_user_org(request.user)
        queryset = filter_vehicles(request, org, base_queryset(org))
        queryset = apply_ordering(queryset, request, ORDERING, ['-created_at'])
        return list_response(
            request, queryset, VehicleOpsSerializer,
            enrich=lambda items: ops_context(org, items),
        )

    @extend_schema(
        summary='Create vehicle',
        description=(
            "Creates a new vehicle under the authenticated "
            "user's organization."
        ),
        request=VehicleSerializer,
        responses={201: VehicleOpsSerializer},
    )
    def post(self, request):
        org = get_user_org(request.user)
        serializer = VehicleSerializer(
            data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        vehicle = serializer.save(organization=org)
        vehicle = base_queryset(org).get(pk=vehicle.pk)
        data = VehicleOpsSerializer(
            vehicle, context={'request': request, **ops_context(org, [vehicle])}
        ).data
        return Response(data, status=status.HTTP_201_CREATED)


@extend_schema_view(
    get=extend_schema(
        summary='Get vehicle by ID',
        description=(
            "Returns a vehicle by its ID (scoped to "
            "user's organization)."
        ),
        responses={200: VehicleOpsSerializer, 404: None},
    ),
    patch=extend_schema(
        summary='Update vehicle',
        description=(
            "Partially updates a vehicle (scoped to "
            "user's organization). Changing status does not touch open jobs; "
            "`open_job_count` in the response shows how many still reference it."
        ),
        request=VehicleUpdateSerializer,
        responses={200: VehicleOpsSerializer, 404: None},
    ),
    delete=extend_schema(
        summary='Delete vehicle',
        description=(
            "Deletes a vehicle (scoped to user's organization). Blocked with "
            "409 if any job has ever used it: retire or sell it instead."
        ),
        responses={204: None, 404: None, 409: None},
    ),
    put=extend_schema(exclude=True),
)
class VehicleDetailView(RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'patch', 'delete']

    def get_serializer_class(self):
        if self.request.method in ('PUT', 'PATCH'):
            return VehicleUpdateSerializer
        return VehicleOpsSerializer

    def get_queryset(self):
        org = get_user_org(self.request.user)
        return base_queryset(org)

    def _ops(self, vehicle):
        org = get_user_org(self.request.user)
        return VehicleOpsSerializer(
            vehicle,
            context={'request': self.request, **ops_context(org, [vehicle])},
        ).data

    def retrieve(self, request, *args, **kwargs):
        return Response(self._ops(self.get_object()))

    def destroy(self, request, *args, **kwargs):
        vehicle = self.get_object()
        used = JobInstance.objects.filter(vehicle=vehicle)
        total = used.count()
        if total:
            open_count = used.filter(status__in=rules.OPEN_STATUSES).count()
            return conflict_response(Conflict(
                f'This vehicle is referenced by {total} job(s) '
                f'({open_count} still open). Deleting it would erase that '
                'history. Mark it Retired or Sold instead.',
                {'job_instances': total, 'open_job_instances': open_count},
            ))
        vehicle.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', True)
        instance = self.get_object()
        serializer = self.get_serializer(
            instance, data=request.data, partial=partial
        )
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        fresh = self.get_queryset().get(pk=instance.pk)
        return Response(self._ops(fresh), status=status.HTTP_200_OK)


class BulkUploadView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(
        summary='Bulk upload vehicles',
        description=(
            'Upload a CSV or Excel file containing vehicle records. '
            'The file is processed asynchronously. Returns a BulkUpload '
            'record with status for polling.'
        ),
        request={
            'multipart/form-data': {
                'type': 'object',
                'properties': {
                    'file': {
                        'type': 'string',
                        'format': 'binary',
                        'description': (
                            'CSV (.csv) or Excel (.xlsx, .xls) file'
                        ),
                    }
                },
                'required': ['file'],
            }
        },
        responses={202: BulkUploadSerializer},
    )
    def post(self, request):
        org = get_user_org(request.user)

        file_obj = request.FILES.get('file')
        if not file_obj:
            raise ValidationError({'file': 'No file provided'})

        ext = (
            file_obj.name.rsplit('.', 1)[-1].lower()
            if '.' in file_obj.name else ''
        )
        if ext not in ('csv', 'xlsx', 'xls'):
            raise ValidationError(
                'Unsupported file format. Use .csv, .xlsx, or .xls'
            )

        bulk = BulkUpload.objects.create(
            organization=org,
            file=file_obj,
        )

        thread = threading.Thread(
            target=_process_bulk_upload,
            args=(bulk.id, ext),
            daemon=True,
        )
        thread.start()

        serializer = BulkUploadSerializer(bulk)
        return Response(serializer.data, status=status.HTTP_202_ACCEPTED)


class BulkUploadStatusView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary='Get bulk upload status',
        description=(
            'Returns the status, progress, and '
            'error report for a bulk upload.'
        ),
        responses={200: BulkUploadStatusSerializer, 404: None},
    )
    def get(self, request, upload_id):
        org = get_user_org(request.user)
        try:
            bulk = BulkUpload.objects.get(id=upload_id, organization=org)
        except BulkUpload.DoesNotExist:
            raise NotFound('Bulk upload not found')

        serializer = BulkUploadStatusSerializer(bulk)
        return Response(serializer.data)


def _process_bulk_upload(bulk_id, ext):
    from django.db import connection

    try:
        bulk = BulkUpload.objects.get(id=bulk_id)
        bulk.status = UploadStatus.PROCESSING
        bulk.save()

        file_path = bulk.file.path
        rows = []

        if ext == 'csv':
            with open(file_path, newline='', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    rows.append(row)
        elif ext in ('xlsx', 'xls'):
            wb = openpyxl.load_workbook(file_path, read_only=True)
            ws = wb.active
            header_row = next(ws.iter_rows(min_row=1, max_row=1))
            headers = [cell.value for cell in header_row]
            for row in ws.iter_rows(min_row=2, values_only=True):
                rows.append(dict(zip(headers, row)))

        bulk.total_rows = len(rows)
        bulk.save()

        errors = []

        for idx, row in enumerate(rows, start=2):
            try:
                mapped = {}
                for csv_key, model_key in VEHICLE_FIELD_MAP.items():
                    value = row.get(csv_key, '')

                    if isinstance(value, str):
                        value = value.strip()

                    if value == '' or value is None:
                        mapped[model_key] = None
                    else:
                        mapped[model_key] = value

                required_fields = [
                    'registration_number', 'manufacturer', 'model',
                    'manufacture_year', 'purchase_date', 'vehicle_type',
                    'fuel_type', 'mileage_kmpl', 'payload_kg',
                    'seating_capacity', 'gvw_kg',
                ]

                missing = [f for f in required_fields if mapped.get(f) is None]
                if missing:
                    raise ValidationError(
                        f'Missing required fields: {", ".join(missing)}'
                    )

                if mapped.get('status') is None:
                    mapped['status'] = VehicleStatus.ACTIVE

                Vehicle.objects.create(
                    organization=bulk.organization, **mapped
                )
                bulk.processed_rows += 1

            except Exception as e:
                bulk.failed_rows += 1
                errors.append({
                    'row': idx,
                    'errors': str(e),
                })

        bulk.error_report = errors
        if bulk.failed_rows == 0:
            bulk.status = UploadStatus.COMPLETED
        elif bulk.processed_rows > 0:
            bulk.status = UploadStatus.COMPLETED
        else:
            bulk.status = UploadStatus.FAILED
        bulk.save()

    except Exception as exc:
        try:
            bulk = BulkUpload.objects.get(id=bulk_id)
            bulk.status = UploadStatus.FAILED
            bulk.error_report = bulk.error_report or []
            bulk.error_report.append(
                {'row': 0, 'errors': f'Processing error: {exc}'}
            )
            bulk.save()
        except BulkUpload.DoesNotExist:
            pass
    finally:
        connection.close()
