"""HTTP layer for the shared evidence store. Business rules live in
`evidence.services`; these views only translate requests and responses."""

import csv
import io
import json

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.api import apply_ordering, get_uuid, get_user_org, list_response
from depots.models import Depot
from evidence import assessments
from evidence import kinds as K
from evidence import services as S
from evidence.models import (
    Assessment,
    EnergyRecord,
    EvidenceSource,
    Observation,
    SourceVerification,
    VerificationStatus,
)
from evidence.serializers import (
    AssessmentSerializer,
    AssessmentSummarySerializer,
    EnergyRecordSerializer,
    EvidenceSourceSerializer,
    ObservationCreateSerializer,
    ObservationSerializer,
    SourceVerifySerializer,
    SupersedeSerializer,
    VoidSerializer,
)
from ev_migration.models import EVCandidate
from vehicles.models import Vehicle

MAX_IMPORT_ROWS = 5000


def _as_of(org):
    """Freshness is judged on the organization's local date."""
    return {'as_of': S.today_for(org)[0]}


class KindListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary='Observation kinds',
                   description='Every kind EVORA accepts, with its canonical '
                   'unit (in the organization\'s currency), subjects, range, '
                   'freshness limit and qualifiers.',
                   responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        org = get_user_org(request.user)
        return Response([k.to_dict(org.currency) for k in K.KINDS.values()])


class SourceListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary='List evidence sources',
                   responses={200: EvidenceSourceSerializer(many=True)})
    def get(self, request):
        org = get_user_org(request.user)
        qs = EvidenceSource.objects.filter(organization=org)
        st = request.query_params.get('verification_status')
        if st:
            qs = qs.filter(verification_status=st)
        qs = apply_ordering(qs, request, {'created_at': 'created_at',
                                          'title': 'title'}, ['-created_at'])
        return list_response(request, qs, EvidenceSourceSerializer)

    @extend_schema(summary='Record an evidence source',
                   request=EvidenceSourceSerializer,
                   responses={201: EvidenceSourceSerializer})
    def post(self, request):
        org = get_user_org(request.user)
        ser = EvidenceSourceSerializer(data=request.data,
                                       context={'request': request})
        ser.is_valid(raise_exception=True)
        source = ser.save(organization=org, created_by=request.user)
        return Response(EvidenceSourceSerializer(source).data,
                        status=status.HTTP_201_CREATED)


class SourceDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary='Get evidence source',
                   responses={200: EvidenceSourceSerializer})
    def get(self, request, pk):
        org = get_user_org(request.user)
        return Response(EvidenceSourceSerializer(
            get_object_or_404(EvidenceSource, organization=org, pk=pk)).data)


class SourceVerifyView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary='Verify or reject a source',
        description='Records that the calling user checked the stored values '
        'against the cited document. External values count as '
        '"verified_external" only after this.',
        request=SourceVerifySerializer,
        responses={200: EvidenceSourceSerializer})
    def post(self, request, pk):
        org = get_user_org(request.user)
        source = get_object_or_404(EvidenceSource, organization=org, pk=pk)
        if source.identifier.startswith('evora-catalogue:'):
            raise ValidationError({'detail': (
                'This source is managed by the EVORA EV catalogue; its '
                'verification is recorded by EVORA staff.')})
        ser = SourceVerifySerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        source.verification_status = ser.validated_data['status']
        source.verification_note = ser.validated_data['note']
        source.verified_by = request.user
        source.verified_at = timezone.now()
        source.save(update_fields=['verification_status',
                                   'verification_note', 'verified_by',
                                   'verified_at'])
        SourceVerification.objects.create(
            source=source, status=source.verification_status,
            note=source.verification_note, decided_by=request.user)
        return Response(EvidenceSourceSerializer(source).data)


def _observation_filters(request, org):
    qs = Observation.objects.filter(organization=org).select_related('source')
    if request.query_params.get('include_superseded') != 'true':
        qs = qs.filter(superseded_by__isnull=True)
    kinds = [k for k in (request.query_params.get('kind') or '').split(',')
             if k]
    if kinds:
        qs = qs.filter(kind__in=kinds)
    for param, column in (('vehicle_id', 'vehicle_id'),
                          ('depot_id', 'depot_id'),
                          ('ev_candidate_id', 'ev_candidate_id')):
        value = get_uuid(request, param)
        if value:
            qs = qs.filter(**{column: value})
    if request.query_params.get('subject') == 'organization':
        qs = qs.filter(vehicle__isnull=True, depot__isnull=True,
                       ev_candidate__isnull=True)
    return qs


class ObservationListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary='List observations',
        description='Current (non-superseded) observations; '
        '`include_superseded=true` adds corrected ones. Filters: kind (comma '
        'list), vehicle_id, depot_id, ev_candidate_id, subject=organization. '
        'Each row reports its evidence class and staleness as of today.',
        responses={200: ObservationSerializer(many=True)})
    def get(self, request):
        org = get_user_org(request.user)
        qs = apply_ordering(
            _observation_filters(request, org), request,
            {'created_at': 'created_at', 'observed_at': 'observed_at',
             'effective_from': 'effective_from'}, ['-created_at'])
        as_of, _ = S.today_for(org)
        return list_response(request, qs, ObservationSerializer,
                             context={'as_of': as_of})

    @extend_schema(summary='Record an observation',
                   request=ObservationCreateSerializer,
                   responses={201: ObservationSerializer})
    def post(self, request):
        org = get_user_org(request.user)
        ser = ObservationCreateSerializer(data=request.data,
                                          context={'request': request})
        ser.is_valid(raise_exception=True)
        obs = S.create_observation(org, request.user, ser.validated_data)
        return Response(ObservationSerializer(obs, context=_as_of(org)).data,
                        status=status.HTTP_201_CREATED)


class ObservationDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary='Get observation',
                   responses={200: ObservationSerializer})
    def get(self, request, pk):
        org = get_user_org(request.user)
        obs = get_object_or_404(Observation.objects.select_related('source'),
                                organization=org, pk=pk)
        return Response(ObservationSerializer(obs, context=_as_of(org)).data)


class ObservationSupersedeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary='Correct an observation',
        description='Records a replacement observation linked to the original. '
        'The original is kept (and stays referenced by any assessment that '
        'used it) but is no longer current.',
        request=SupersedeSerializer, responses={201: ObservationSerializer})
    def post(self, request, pk):
        org = get_user_org(request.user)
        with transaction.atomic():
            original = get_object_or_404(
                Observation.objects.select_for_update(), organization=org,
                pk=pk)
            if Observation.objects.filter(supersedes=original).exists():
                raise ValidationError(
                    'This observation has already been superseded; correct '
                    'the current one instead.')
            ser = SupersedeSerializer(data=request.data,
                                      context={'request': request})
            ser.is_valid(raise_exception=True)
            data = dict(ser.validated_data)
            if data['kind'] != original.kind:
                raise ValidationError(
                    {'kind': 'A correction must keep the same kind.'})
            for field in ('vehicle', 'depot', 'ev_candidate'):
                if getattr(data.get(field), 'pk', None) != getattr(
                        original, f'{field}_id'):
                    raise ValidationError(
                        {f'{field}_id': 'A correction must describe the same '
                         'subject.'})
            data['supersedes'] = original
            obs = S.create_observation(org, request.user, data)
        return Response(ObservationSerializer(obs, context=_as_of(org)).data,
                        status=status.HTTP_201_CREATED)


IMPORT_COLUMNS = (
    'kind', 'value', 'unit', 'vehicle_registration', 'depot_name',
    'ev_candidate_id', 'qualifiers', 'origin', 'observed_at', 'period_start',
    'period_end', 'effective_from', 'effective_to', 'note',
)


def _parse_import_row(org, row, source, lookups):
    errors = {}
    data = {'origin': (row.get('origin') or '').strip(), 'source': source,
            'note': (row.get('note') or '').strip()}
    for key in ('kind', 'unit'):
        data[key] = (row.get(key) or '').strip()
    try:
        data['value'] = float(row.get('value'))
    except (TypeError, ValueError):
        errors['value'] = 'Must be a number.'
    reg = (row.get('vehicle_registration') or '').strip()
    if reg:
        data['vehicle'] = lookups['vehicles'].get(reg.lower())
        if data['vehicle'] is None:
            errors['vehicle_registration'] = f'No vehicle "{reg}".'
    depot = (row.get('depot_name') or '').strip()
    if depot:
        data['depot'] = lookups['depots'].get(depot.lower())
        if data['depot'] is None:
            errors['depot_name'] = f'No depot "{depot}" (or more than one).'
    cand = (row.get('ev_candidate_id') or '').strip()
    if cand:
        data['ev_candidate'] = lookups['candidates'].get(cand)
        if data['ev_candidate'] is None:
            errors['ev_candidate_id'] = 'Not found.'
    raw_q = (row.get('qualifiers') or '').strip()
    try:
        data['qualifiers'] = json.loads(raw_q) if raw_q else {}
    except json.JSONDecodeError:
        errors['qualifiers'] = 'Must be a JSON object, e.g. {"fuel": "diesel"}.'
    raw_at = (row.get('observed_at') or '').strip()
    if raw_at:
        at = parse_datetime(raw_at)
        if at is None:
            errors['observed_at'] = 'Use ISO 8601 with a timezone offset.'
        elif timezone.is_naive(at):
            errors['observed_at'] = 'Include a timezone offset (e.g. +05:30).'
        data['observed_at'] = at
    for key in ('period_start', 'period_end', 'effective_from',
                'effective_to'):
        raw = (row.get(key) or '').strip()
        if raw:
            parsed = parse_date(raw)
            if parsed is None:
                errors[key] = 'Use YYYY-MM-DD.'
            data[key] = parsed
    if errors:
        raise ValidationError(errors)
    return data


class ObservationImportView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(
        summary='Import observations from CSV (all or nothing)',
        description='CSV columns: ' + ', '.join(IMPORT_COLUMNS) + '. Every row '
        'cites the one `source_id` sent with the file. Every row is validated '
        'with the same rules as a single observation; if any row fails, '
        'nothing is stored and every failing row is reported.',
        request={'multipart/form-data': {
            'type': 'object',
            'properties': {'file': {'type': 'string', 'format': 'binary'},
                           'source_id': {'type': 'string',
                                         'format': 'uuid'}},
            'required': ['file', 'source_id']}},
        responses={201: None, 400: None})
    def post(self, request):
        org = get_user_org(request.user)
        source = EvidenceSource.objects.filter(
            organization=org, pk=request.data.get('source_id')).first() \
            if request.data.get('source_id') else None
        if source is None:
            raise ValidationError({'source_id': 'A valid source is required.'})
        if source.verification_status == VerificationStatus.REJECTED:
            raise ValidationError({'source_id': 'This source was rejected.'})
        upload = request.FILES.get('file')
        if upload is None or not upload.name.lower().endswith('.csv'):
            raise ValidationError({'file': 'Upload a .csv file.'})
        try:
            text = upload.read().decode('utf-8-sig')
        except UnicodeDecodeError:
            raise ValidationError({'file': 'File must be UTF-8 text.'})
        reader = csv.DictReader(io.StringIO(text))
        unknown = set(reader.fieldnames or []) - set(IMPORT_COLUMNS)
        if unknown or not reader.fieldnames:
            raise ValidationError({'file': (
                f'Unknown or missing columns {sorted(unknown)}; expected '
                f'{", ".join(IMPORT_COLUMNS)}.')})
        rows = list(reader)
        if not rows:
            raise ValidationError({'file': 'The file has no data rows.'})
        if len(rows) > MAX_IMPORT_ROWS:
            raise ValidationError(
                {'file': f'At most {MAX_IMPORT_ROWS} rows per import.'})

        depots = {}
        for d in Depot.objects.filter(organization=org):
            depots[d.name.lower()] = None if d.name.lower() in depots else d
        lookups = {
            'vehicles': {v.registration_number.lower(): v for v in
                         Vehicle.objects.filter(organization=org)},
            'depots': depots,
            'candidates': {str(c.pk): c for c in
                           EVCandidate.objects.filter(organization=org)},
        }
        failures, created = [], []
        try:
            with transaction.atomic():
                for line, row in enumerate(rows, start=2):
                    try:
                        data = _parse_import_row(org, row, source, lookups)
                        created.append(S.create_observation(
                            org, request.user, data))
                    except ValidationError as exc:
                        failures.append({'row': line, 'errors': exc.detail})
                if failures:
                    raise _Rollback()
        except _Rollback:
            return Response(
                {'detail': f'{len(failures)} of {len(rows)} rows failed; '
                 'nothing was imported.', 'failed_rows': failures},
                status=status.HTTP_400_BAD_REQUEST)
        return Response({'imported': len(created),
                         'source_id': str(source.pk),
                         'observation_ids': [str(o.pk) for o in created]},
                        status=status.HTTP_201_CREATED)


class _Rollback(Exception):
    pass


class EnergyRecordListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary='List fuel fills and charging sessions',
                   description='Filters: vehicle_id, record_type, '
                   'include_voided=true.',
                   responses={200: EnergyRecordSerializer(many=True)})
    def get(self, request):
        org = get_user_org(request.user)
        qs = EnergyRecord.objects.filter(organization=org)
        if request.query_params.get('include_voided') != 'true':
            qs = qs.filter(voided_at__isnull=True)
        vid = get_uuid(request, 'vehicle_id')
        if vid:
            qs = qs.filter(vehicle_id=vid)
        rtype = request.query_params.get('record_type')
        if rtype:
            qs = qs.filter(record_type=rtype)
        qs = apply_ordering(qs, request, {'occurred_at': 'occurred_at'},
                            ['-occurred_at'])
        return list_response(request, qs, EnergyRecordSerializer)

    @extend_schema(summary='Record a fuel fill or charging session',
                   request=EnergyRecordSerializer,
                   responses={201: EnergyRecordSerializer})
    def post(self, request):
        org = get_user_org(request.user)
        ser = EnergyRecordSerializer(data=request.data,
                                     context={'request': request})
        ser.is_valid(raise_exception=True)
        record = ser.save(organization=org, created_by=request.user)
        return Response(EnergyRecordSerializer(record).data,
                        status=status.HTTP_201_CREATED)


class EnergyRecordVoidView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary='Void a fuel/charging record (kept, not deleted)',
                   request=VoidSerializer,
                   responses={200: EnergyRecordSerializer})
    def post(self, request, pk):
        org = get_user_org(request.user)
        record = get_object_or_404(EnergyRecord, organization=org, pk=pk)
        if record.voided_at:
            raise ValidationError('Already voided.')
        ser = VoidSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        record.voided_at = timezone.now()
        record.void_reason = ser.validated_data['reason']
        record.voided_by = request.user
        record.save(update_fields=['voided_at', 'void_reason', 'voided_by'])
        return Response(EnergyRecordSerializer(record).data)


class VehicleUsageView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary='Vehicle usage profile',
        description='Usage computed live from measured records (odometer '
        'readings, fuel/charging records) for `window_days` (default 365) '
        'ending now, plus planned distance from completed jobs and the '
        'vehicle record\'s manual fields - each labelled separately. Not '
        'persisted. Definitions: see evidence/usage.py (usage/1.0.0).',
        responses={200: OpenApiTypes.OBJECT})
    def get(self, request, pk):
        org = get_user_org(request.user)
        vehicle = get_object_or_404(Vehicle, organization=org, pk=pk)
        start, end, days = S.window_from_request(request, org)
        _, tz = S.today_for(org)
        return Response(S.vehicle_usage(org, vehicle, start, end, tz))


class AssessmentListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary='List assessments (summaries)',
                   description='Filters: kind (comma list), vehicle_id, '
                   'ev_candidate_id.',
                   responses={200: AssessmentSummarySerializer(many=True)})
    def get(self, request):
        org = get_user_org(request.user)
        qs = Assessment.objects.filter(organization=org)
        kinds = [k for k in (request.query_params.get('kind') or '').split(
            ',') if k]
        if kinds:
            qs = qs.filter(kind__in=kinds)
        for p in ('vehicle_id', 'ev_candidate_id'):
            value = get_uuid(request, p)
            if value:
                qs = qs.filter(**{p: value})
        qs = apply_ordering(qs, request, {'created_at': 'created_at'},
                            ['-created_at'])
        return list_response(request, qs, AssessmentSummarySerializer)


class AssessmentDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary='Get assessment with full snapshot',
                   responses={200: AssessmentSerializer})
    def get(self, request, pk):
        org = get_user_org(request.user)
        return Response(AssessmentSerializer(
            get_object_or_404(Assessment, organization=org, pk=pk)).data)


class AssessmentReproduceView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary='Recompute an assessment from its stored snapshot',
        description='Runs the stored method version on the stored inputs and '
        'reports whether the result matches. Nothing is written.',
        request=None, responses={200: OpenApiTypes.OBJECT})
    def post(self, request, pk):
        org = get_user_org(request.user)
        a = get_object_or_404(Assessment, organization=org, pk=pk)
        return Response(assessments.reproduce(a))
