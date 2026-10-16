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
    list_response, selected_day,
)
from drivers.models import BulkUpload, Driver, DriverStatus, UploadStatus
from drivers.serializers import (
    BulkUploadSerializer,
    BulkUploadStatusSerializer,
    DriverOpsSerializer,
    DriverSerializer,
    DriverUpdateSerializer,
)
from job_instances.models import JobInstance
from job_instances.services import open_jobs_by
from operations import rules


def get_user_org(user):
    org = getattr(user, 'organization', None)
    if org is None:
        raise PermissionDenied('User not associated with any organization')
    return org


DRIVER_FIELD_MAP = {
    'driver_code': 'driver_code',
    'name': 'name',
    'date_of_birth': 'date_of_birth',
    'license_number': 'license_number',
    'license_class': 'license_class',
    'license_expiry': 'license_expiry',
    'experience_years': 'experience_years',
    'job_id': 'job_id',
    'status': 'status',
    'shift': 'shift',
    'emergency_contact_name': 'emergency_contact_name',
    'emergency_contact_phone': 'emergency_contact_phone',
    'languages': 'languages',
    'user_id': 'user_id',
}


ORDERING = {
    'name': 'name',
    'driver_code': 'driver_code',
    'status': 'status',
    'license_expiry': 'license_expiry',
    'experience_years': 'experience_years',
    'shift': 'shift',
    'created_at': 'created_at',
}
AVAILABILITY = ('available', 'assigned', 'unavailable')


def filter_drivers(request, org, queryset):
    search = get_search(request)
    if search:
        queryset = queryset.filter(
            Q(name__icontains=search)
            | Q(driver_code__icontains=search)
            | Q(license_number__icontains=search)
        )
    statuses = get_choice_list(request, 'status', DriverStatus.values)
    if statuses:
        queryset = queryset.filter(status__in=statuses)
    shift = (request.query_params.get('shift') or '').strip()
    if shift:
        queryset = queryset.filter(shift__iexact=shift)

    availability = request.query_params.get('availability')
    if availability:
        if availability not in AVAILABILITY:
            raise ValidationError({'availability': f'Use one of: {", ".join(AVAILABILITY)}.'})
        _, _, day, start, end = selected_day(request, org)
        usable = rules.driver_usable_q(day)
        committed = rules.committed_driver_ids(org, start, end)
        if availability == 'unavailable':
            queryset = queryset.exclude(usable)
        elif availability == 'assigned':
            queryset = queryset.filter(usable, pk__in=committed)
        else:
            queryset = queryset.filter(usable).exclude(pk__in=committed)
    return queryset


def ops_context(org, drivers):
    return {'open_jobs': open_jobs_by('driver', [d.pk for d in drivers], org)}


class DriverListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary='List drivers',
        description=(
            "Returns drivers for the authenticated user's organization. "
            'Without query parameters a bare array (legacy); with '
            '`page`/`page_size` a page envelope including the current job. '
            'Filters: `search`, `status` (comma list), `shift`, `availability` '
            '(available|assigned|unavailable, relative to `date`), `ordering`.'
        ),
        responses={200: DriverOpsSerializer(many=True)},
    )
    def get(self, request):
        org = get_user_org(request.user)
        drivers = filter_drivers(request, org, Driver.objects.filter(organization=org))
        drivers = apply_ordering(drivers, request, ORDERING, ['-created_at'])
        return list_response(
            request, drivers, DriverOpsSerializer,
            enrich=lambda items: ops_context(org, items),
        )

    @extend_schema(
        summary='Create driver',
        description=(
            "Creates a new driver under the authenticated "
            "user's organization."
        ),
        request=DriverSerializer,
        responses={201: DriverOpsSerializer},
    )
    def post(self, request):
        org = get_user_org(request.user)
        serializer = DriverSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        driver = serializer.save(organization=org)
        data = DriverOpsSerializer(
            driver, context={'request': request, **ops_context(org, [driver])}
        ).data
        return Response(data, status=status.HTTP_201_CREATED)


@extend_schema_view(
    get=extend_schema(
        summary='Get driver by ID',
        description=(
            "Returns a driver by its ID (scoped to "
            "user's organization)."
        ),
        responses={200: DriverOpsSerializer, 404: None},
    ),
    patch=extend_schema(
        summary='Update driver',
        description=(
            "Partially updates a driver (scoped to "
            "user's organization). Changing status does not touch open jobs; "
            "`open_job_count` in the response shows how many still reference them."
        ),
        request=DriverUpdateSerializer,
        responses={200: DriverOpsSerializer, 404: None},
    ),
    delete=extend_schema(
        summary='Delete driver',
        description=(
            "Deletes a driver (scoped to user's organization). Blocked with "
            "409 if any job has ever used them: set the status to Terminated "
            "or Inactive instead."
        ),
        responses={204: None, 404: None, 409: None},
    ),
    put=extend_schema(exclude=True),
)
class DriverDetailView(RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'patch', 'delete']

    def get_serializer_class(self):
        if self.request.method in ('PUT', 'PATCH'):
            return DriverUpdateSerializer
        return DriverOpsSerializer

    def get_queryset(self):
        org = get_user_org(self.request.user)
        return Driver.objects.filter(organization=org)

    def _ops(self, driver):
        org = get_user_org(self.request.user)
        return DriverOpsSerializer(
            driver,
            context={'request': self.request, **ops_context(org, [driver])},
        ).data

    def retrieve(self, request, *args, **kwargs):
        return Response(self._ops(self.get_object()))

    def destroy(self, request, *args, **kwargs):
        driver = self.get_object()
        used = JobInstance.objects.filter(driver=driver)
        total = used.count()
        if total:
            open_count = used.filter(status__in=rules.OPEN_STATUSES).count()
            return conflict_response(Conflict(
                f'This driver is referenced by {total} job(s) '
                f'({open_count} still open). Deleting them would erase that '
                'history. Set their status to Terminated or Inactive instead.',
                {'job_instances': total, 'open_job_instances': open_count},
            ))
        driver.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', True)
        instance = self.get_object()
        serializer = self.get_serializer(
            instance, data=request.data, partial=partial
        )
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return Response(self._ops(instance), status=status.HTTP_200_OK)


class BulkUploadView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(
        summary='Bulk upload drivers',
        description=(
            'Upload a CSV or Excel file containing driver records. '
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
                for csv_key, model_key in DRIVER_FIELD_MAP.items():
                    value = row.get(csv_key, '')

                    if isinstance(value, str):
                        value = value.strip()

                    if value != '' and value is not None:
                        mapped[model_key] = value

                required_fields = [
                    'driver_code', 'name', 'date_of_birth',
                    'license_number', 'license_class', 'license_expiry',
                    'experience_years',
                ]

                missing = [f for f in required_fields if f not in mapped]
                if missing:
                    raise ValidationError(
                        f'Missing required fields: {", ".join(missing)}'
                    )

                mapped.setdefault('status', DriverStatus.ACTIVE)

                if isinstance(mapped.get('languages'), str):
                    mapped['languages'] = [
                        lang.strip()
                        for lang in mapped['languages'].split(',')
                        if lang.strip()
                    ]

                Driver.objects.create(
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
