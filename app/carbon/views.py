import re

from django.http import HttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from carbon.footprint import (
    available_boundaries, footprint, parse_period, SCENARIO_SHARES)
from carbon.report import build_pdf
from common.api import get_user_org
from ev_migration.calc.emissions import COMPARABILITY

PARAMS = [
    OpenApiParameter('start', OpenApiTypes.DATE),
    OpenApiParameter('end', OpenApiTypes.DATE),
    OpenApiParameter('fuel_boundary', OpenApiTypes.STR,
                     enum=['tank_to_wheel', 'well_to_wheel']),
    OpenApiParameter('grid_boundary', OpenApiTypes.STR,
                     enum=['generation', 'consumption_incl_td_losses']),
]


def _inputs(request):
    org = get_user_org(request.user)
    try:
        start, end = parse_period(request.query_params, org)
    except ValueError as e:
        raise ValidationError({'period': str(e)})
    fb = request.query_params.get('fuel_boundary') or 'tank_to_wheel'
    gb = request.query_params.get('grid_boundary') or 'generation'
    if (fb, gb) not in COMPARABILITY:
        raise ValidationError({'boundary': 'Unknown boundary pair.'})
    return org, start, end, fb, gb


class FootprintView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary='Fleet carbon footprint for a period',
                   parameters=PARAMS, responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        org, start, end, fb, gb = _inputs(request)
        data = footprint(org, start, end, fb, gb)
        data['boundaries'] = available_boundaries(org, end)
        return Response(data)


class ReportView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary='Download the carbon report (PDF)',
        parameters=PARAMS + [OpenApiParameter(
            'scenario_pct', OpenApiTypes.INT, enum=list(SCENARIO_SHARES))],
        responses={(200, 'application/pdf'): OpenApiTypes.BINARY})
    def get(self, request):
        org, start, end, fb, gb = _inputs(request)
        try:
            share = int(request.query_params.get('scenario_pct', 50))
        except ValueError:
            share = 0
        if share not in SCENARIO_SHARES:
            raise ValidationError({'scenario_pct': 'Use 25, 50, 75 or 100.'})
        pdf = build_pdf(org, request.user,
                        footprint(org, start, end, fb, gb), share)
        slug = re.sub(r'[^a-z0-9]+', '-', org.name.lower()).strip('-') or 'org'
        resp = HttpResponse(pdf, content_type='application/pdf')
        resp['Content-Disposition'] = (
            f'attachment; filename="evora-carbon-report-{slug}-'
            f'{start.isoformat()}-to-{end.isoformat()}.pdf"')
        return resp
