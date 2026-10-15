"""Shared helpers for list endpoints used by the Operations workspaces.

Design rule: every behaviour here is *opt-in*. A request with no query
parameters must return exactly what it returned before (a bare JSON array),
because the Dashboard's shared data provider depends on that shape.
"""

import math
from datetime import date as date_cls
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone as dj_timezone
from rest_framework import status
from rest_framework.exceptions import (
    APIException,
    PermissionDenied,
    ValidationError,
)
from rest_framework.response import Response

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100


class Conflict(APIException):
    """409 - the request is valid but blocked by the current state of the data."""

    status_code = status.HTTP_409_CONFLICT
    default_detail = (
        'The request conflicts with the current state of the data.'
    )
    default_code = 'conflict'

    def __init__(self, detail=None, blockers=None):
        super().__init__(detail)
        self.blockers = blockers or {}


def conflict_response(exc):
    return Response(
        {'detail': str(exc.detail), 'blockers': exc.blockers},
        status=status.HTTP_409_CONFLICT,
    )


def get_user_org(user):
    org = getattr(user, 'organization', None)
    if org is None:
        raise PermissionDenied('User not associated with any organization')
    return org


# --------------------------------------------------------------------------
# Query-string parsing
# --------------------------------------------------------------------------


def get_csv(request, name):
    """`?status=A,B&status=C` -> ['A', 'B', 'C'] (empty list when absent)."""
    values = []
    for raw in request.query_params.getlist(name):
        values.extend(part.strip() for part in raw.split(',') if part.strip())
    return values


def get_choice_list(request, name, choices):
    values = get_csv(request, name)
    invalid = [v for v in values if v not in choices]
    if invalid:
        raise ValidationError(
            {name: f'Invalid value(s): {", ".join(invalid)}.'}
        )
    return values


def get_uuid(request, name):
    import uuid

    raw = request.query_params.get(name)
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise ValidationError({name: 'Must be a valid UUID.'})


def get_search(request):
    return (request.query_params.get('search') or '').strip()


def _positive_int(request, name, default):
    raw = request.query_params.get(name)
    if raw in (None, ''):
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValidationError({name: 'Must be an integer.'})
    if value < 1:
        raise ValidationError({name: 'Must be 1 or greater.'})
    return value


def wants_page(request):
    return (
        'page' in request.query_params or 'page_size' in request.query_params
    )


def apply_ordering(queryset, request, allowed, default):
    """Order by a whitelist. `allowed` maps public name -> ORM expression.

    `ordering=-name,created_at` is accepted. A stable `pk` tie-breaker is
    always appended so pages never overlap or skip rows.
    """
    raw = (request.query_params.get('ordering') or '').strip()
    fields = [
        part.strip() for part in raw.split(',') if part.strip()
    ] or default
    terms = []
    for field in fields:
        desc = field.startswith('-')
        key = field.lstrip('-')
        if key not in allowed:
            raise ValidationError(
                {
                    'ordering': f'Unsupported ordering "{key}". Allowed: {", ".join(sorted(allowed))}.'
                }
            )
        expr = allowed[key]
        terms.append(f'-{expr}' if desc else expr)
    terms.append('pk')
    return queryset.order_by(*terms)


def paginate_queryset(request, queryset):
    size = min(
        _positive_int(request, 'page_size', DEFAULT_PAGE_SIZE), MAX_PAGE_SIZE
    )
    page = _positive_int(request, 'page', 1)
    count = queryset.count()
    total_pages = max(1, math.ceil(count / size))
    page = min(page, total_pages)
    start = (page - 1) * size
    return list(queryset[start : start + size]), {
        'count': count,
        'page': page,
        'page_size': size,
        'total_pages': total_pages,
    }


def list_response(
    request,
    queryset,
    serializer_class,
    context=None,
    *,
    enrich=None,
    always_enrich=False,
):
    """Return a bare array (legacy) or a page envelope when the client asks.

    `enrich(items)` runs on the page items before serialisation and may return
    extra serializer context (used to attach related rows without N+1 queries).
    For a bare-array response it only runs when `?expand=ops` is sent (so the
    Dashboard's plain list stays exactly as cheap as before) unless the
    endpoint is new and passes `always_enrich=True`.
    """
    ctx = dict(context or {})
    ctx.setdefault('request', request)

    if wants_page(request):
        items, meta = paginate_queryset(request, queryset)
        if enrich:
            ctx.update(enrich(items) or {})
        data = serializer_class(items, many=True, context=ctx).data
        return Response({**meta, 'results': data})

    items = list(queryset)
    if enrich and (
        always_enrich or request.query_params.get('expand') == 'ops'
    ):
        ctx.update(enrich(items) or {})
    return Response(serializer_class(items, many=True, context=ctx).data)


# --------------------------------------------------------------------------
# Time handling
# --------------------------------------------------------------------------


def resolve_timezone(request, org):
    """Timezone used for "what day is it" questions.

    Order: explicit `?tz=` (the browser's zone, which is what the Dashboard
    already uses for its day buckets) -> the organisation's configured zone
    -> UTC. Unknown zone names are ignored, never fatal.
    """
    for name in (
        request.query_params.get('tz'),
        getattr(org, 'timezone', None),
        'UTC',
    ):
        if not name:
            continue
        try:
            return ZoneInfo(name), name
        except (ZoneInfoNotFoundError, ValueError, OSError):
            continue
    return ZoneInfo('UTC'), 'UTC'


def parse_date(request, name):
    raw = request.query_params.get(name)
    if not raw:
        return None
    try:
        return date_cls.fromisoformat(raw)
    except ValueError:
        raise ValidationError({name: 'Use YYYY-MM-DD.'})


def local_day_range(day, tz):
    """[start, end) of a calendar day in `tz`, as aware UTC datetimes."""
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)
    utc = ZoneInfo('UTC')
    return start.astimezone(utc), end.astimezone(utc)


def requested_window(request, org):
    """Resolve `?date=` or `?date_from=&date_to=` into a UTC [start, end).

    Returns (start, end, info) where start/end may be None (no date filter).
    """
    tz, tz_name = resolve_timezone(request, org)
    day = parse_date(request, 'date')
    date_from = parse_date(request, 'date_from')
    date_to = parse_date(request, 'date_to')

    if day and (date_from or date_to):
        raise ValidationError(
            'Use either date or date_from/date_to, not both.'
        )

    if day:
        start, end = local_day_range(day, tz)
        return start, end, {'timezone': tz_name}

    if date_from or date_to:
        if date_from and date_to and date_to < date_from:
            raise ValidationError({'date_to': 'Must not be before date_from.'})
        start = local_day_range(date_from, tz)[0] if date_from else None
        end = local_day_range(date_to, tz)[1] if date_to else None
        return start, end, {'timezone': tz_name}

    return None, None, {'timezone': tz_name}


def today_in(tz):
    return dj_timezone.now().astimezone(tz).date()


def selected_day(request, org):
    """(tz, tz_name, day, start_utc, end_utc) for `?date=` (default: today)."""
    tz, tz_name = resolve_timezone(request, org)
    day = parse_date(request, 'date') or today_in(tz)
    start, end = local_day_range(day, tz)
    return tz, tz_name, day, start, end
