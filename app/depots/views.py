import csv
import json
import threading

import openpyxl
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

from depots.models import BulkUpload, Depot, UploadStatus
from depots.serializers import (
    BulkUploadSerializer,
    BulkUploadStatusSerializer,
    DepotSerializer,
    DepotUpdateSerializer,
)


def get_user_org(user):
    org = getattr(user, 'organization', None)
    if org is None:
        raise PermissionDenied('User not associated with any organization')
    return org


DEPOT_FIELD_MAP = {
    'name': 'name',
    'address': 'address',
    'parking_capacity': 'parking_capacity',
    'workshop_available': 'workshop_available',
    'fuel_station_available': 'fuel_station_available',
    'charging_available': 'charging_available',
    'charger_count': 'charger_count',
    'maintenance_bays': 'maintenance_bays',
    'operating_hours': 'operating_hours',
    'depot_manager_name': 'depot_manager_name',
    'depot_manager_contact': 'depot_manager_contact',
}


class DepotListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary='List depots',
        description=(
            "Returns all depots belonging to the "
            "authenticated user's organization."
        ),
        responses={200: DepotSerializer(many=True)},
    )
    def get(self, request):
        org = get_user_org(request.user)
        depots = Depot.objects.filter(
            organization=org
        ).order_by('-created_at')
        serializer = DepotSerializer(depots, many=True)
        return Response(serializer.data)

    @extend_schema(
        summary='Create depot',
        description=(
            "Creates a new depot under the authenticated "
            "user's organization."
        ),
        request=DepotSerializer,
        responses={201: DepotSerializer},
    )
    def post(self, request):
        org = get_user_org(request.user)
        serializer = DepotSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(organization=org)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


@extend_schema_view(
    get=extend_schema(
        summary='Get depot by ID',
        description=(
            "Returns a depot by its ID (scoped to "
            "user's organization)."
        ),
        responses={200: DepotSerializer, 404: None},
    ),
    patch=extend_schema(
        summary='Update depot',
        description=(
            "Partially updates a depot (scoped to "
            "user's organization)."
        ),
        request=DepotUpdateSerializer,
        responses={200: DepotSerializer, 404: None},
    ),
    delete=extend_schema(
        summary='Delete depot',
        description=(
            "Deletes a depot (scoped to "
            "user's organization)."
        ),
        responses={204: None, 404: None},
    ),
    put=extend_schema(exclude=True),
)
class DepotDetailView(RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'patch', 'delete']

    def get_serializer_class(self):
        if self.request.method in ('PUT', 'PATCH'):
            return DepotUpdateSerializer
        return DepotSerializer

    def get_queryset(self):
        org = get_user_org(self.request.user)
        return Depot.objects.filter(organization=org)

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
        data = DepotSerializer(instance).data
        return Response(data, status=status.HTTP_200_OK)


class BulkUploadView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(
        summary='Bulk upload depots',
        description=(
            'Upload a CSV or Excel file containing depot records. '
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
                for csv_key, model_key in DEPOT_FIELD_MAP.items():
                    value = row.get(csv_key, '')

                    if isinstance(value, str):
                        value = value.strip()

                    if value != '' and value is not None:
                        mapped[model_key] = value

                required_fields = ['name', 'address']
                missing = [f for f in required_fields if f not in mapped]
                if missing:
                    raise ValidationError(
                        f'Missing required fields: {", ".join(missing)}'
                    )

                boolean_fields = [
                    'workshop_available',
                    'fuel_station_available',
                    'charging_available',
                ]
                for field in boolean_fields:
                    if field in mapped:
                        if isinstance(mapped[field], str):
                            mapped[field] = mapped[field].lower() in (
                                'true', '1', 'yes', 'y'
                            )
                        else:
                            mapped[field] = bool(mapped[field])

                integer_fields = [
                    'parking_capacity', 'charger_count', 'maintenance_bays',
                ]
                for field in integer_fields:
                    if field in mapped:
                        try:
                            mapped[field] = int(mapped[field])
                        except (ValueError, TypeError):
                            raise ValidationError(
                                f'{field} must be an integer'
                            )

                if 'operating_hours' in mapped and isinstance(mapped['operating_hours'], str):
                    try:
                        mapped['operating_hours'] = json.loads(mapped['operating_hours'])
                    except json.JSONDecodeError:
                        raise ValidationError(
                            'operating_hours must be a valid JSON object'
                        )

                Depot.objects.create(
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
