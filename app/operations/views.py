"""Read-only aggregates for the Operations overview.

Every number here is a COUNT over stored rows with the definitions in
`operations.rules`; nothing is estimated. "Day" means the calendar day of the
`date` parameter in the `tz` timezone (browser zone, like the Dashboard).
"""

from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.api import get_user_org, local_day_range, selected_day
from depots.models import Depot
from drivers.models import Driver
from fleets.models import Fleet
from job_instances.models import JobInstance, JobInstanceStatus
from job_templates.models import JobTemplate
from operations import rules
from vehicles.models import Vehicle

S = JobInstanceStatus
PERFORMANCE_DAYS = 7
ATTENTION_CAP = 200


def _partition(model, usable_q, committed_ids, org):
    total = model.objects.filter(organization=org).count()
    usable_ids = set(
        model.objects.filter(organization=org)
        .filter(usable_q)
        .values_list('pk', flat=True)
    )
    assigned = len(usable_ids & committed_ids)
    return {
        'total': total,
        'available': len(usable_ids) - assigned,
        'assigned': assigned,
        'unavailable': total - len(usable_ids),
        'usable': len(usable_ids),
    }


def _rate(numerator, denominator):
    return {
        'numerator': numerator,
        'denominator': denominator,
        'rate': (
            round(numerator / denominator * 100, 1) if denominator else None
        ),
    }


def build_attention(org, tz, day):
    """Actionable problems for jobs scheduled on `day`. Returns (items, jobs_flagged)."""
    start, end = local_day_range(day, tz)
    now = timezone.now()
    jobs = list(
        rules.day_jobs(org, start, end)
        .filter(status__in=rules.OPEN_STATUSES)
        .select_related('job_template', 'vehicle', 'driver')
        .order_by('scheduled_start_at')
    )
    # Everything open that overlaps the day, to detect double bookings that
    # started the evening before.
    neighbours = list(
        JobInstance.objects.filter(
            organization=org,
            status__in=rules.OPEN_STATUSES,
            scheduled_start_at__isnull=False,
            scheduled_start_at__lt=end,
        )
        .filter(
            Q(scheduled_end_at__gt=start)
            | Q(scheduled_end_at__isnull=True, scheduled_start_at__gte=start)
        )
        .select_related('job_template')
    )

    items = []

    def add(kind, severity, job, message, action, **extra):
        items.append(
            {
                'id': f'{kind}:{job.pk}',
                'kind': kind,
                'severity': severity,
                'job_id': job.pk,
                'job_name': job.job_template.name,
                'scheduled_start_at': job.scheduled_start_at,
                'message': message,
                'action': action,
                **extra,
            }
        )

    for job in jobs:
        late = (
            job.scheduled_start_at is not None and job.scheduled_start_at < now
        )
        sev = 'critical' if late else 'warning'
        assign_action = {'type': 'assign_job', 'label': 'Assign'}

        if job.status == S.PENDING:
            if not job.vehicle_id and not job.driver_id:
                add(
                    'unassigned_job',
                    sev,
                    job,
                    'Job is awaiting assignment.',
                    assign_action,
                )
            elif not job.vehicle_id:
                add(
                    'missing_vehicle',
                    sev,
                    job,
                    'No vehicle assigned yet.',
                    {'type': 'assign_job', 'label': 'Assign vehicle'},
                    driver_id=job.driver_id,
                )
            elif not job.driver_id:
                add(
                    'missing_driver',
                    sev,
                    job,
                    'No driver assigned yet.',
                    {'type': 'assign_job', 'label': 'Assign driver'},
                    vehicle_id=job.vehicle_id,
                )

        on_date = (
            rules.local_date_of(job.scheduled_start_at, tz)
            if job.scheduled_start_at
            else day
        )
        if job.vehicle_id:
            problems = rules.vehicle_blockers(job.vehicle, on_date)
            if problems:
                add(
                    'vehicle_unavailable',
                    'critical',
                    job,
                    f'{job.vehicle.registration_number}: {problems[0]}',
                    {'type': 'open_vehicle', 'label': 'Open vehicle'},
                    vehicle_id=job.vehicle_id,
                )
        if job.driver_id:
            problems = rules.driver_blockers(job.driver, on_date)
            if problems:
                add(
                    'driver_unavailable',
                    'critical',
                    job,
                    f'{job.driver.name}: {problems[0]}',
                    {'type': 'open_driver', 'label': 'Open driver'},
                    driver_id=job.driver_id,
                )

        if job.status == S.ASSIGNED and late:
            add(
                'late_start',
                'warning',
                job,
                'Scheduled start has passed but the job has not started.',
                {'type': 'open_job', 'label': 'Open job'},
            )
        if (
            job.status == S.IN_PROGRESS
            and job.scheduled_end_at
            and job.scheduled_end_at < now
        ):
            add(
                'running_over',
                'warning',
                job,
                'Job is still running past its scheduled end.',
                {'type': 'open_job', 'label': 'Open job'},
            )

        # Double bookings (same vehicle / driver, overlapping windows).
        if job.scheduled_start_at:
            s, e = rules.job_window(job)
            for other in neighbours:
                if other.pk == job.pk:
                    continue
                os_, oe = rules.job_window(other)
                if not rules.windows_overlap(s, e, os_, oe):
                    continue
                if job.vehicle_id and job.vehicle_id == other.vehicle_id:
                    add(
                        'vehicle_conflict',
                        'critical',
                        job,
                        f'Vehicle is also booked on \u201c{other.job_template.name}\u201d at the same time.',
                        {'type': 'assign_job', 'label': 'Reassign'},
                        vehicle_id=job.vehicle_id,
                    )
                    break
            for other in neighbours:
                if other.pk == job.pk or not (
                    job.driver_id and job.driver_id == other.driver_id
                ):
                    continue
                os_, oe = rules.job_window(other)
                if rules.windows_overlap(s, e, os_, oe):
                    add(
                        'driver_conflict',
                        'critical',
                        job,
                        f'Driver is also booked on \u201c{other.job_template.name}\u201d at the same time.',
                        {'type': 'assign_job', 'label': 'Reassign'},
                        driver_id=job.driver_id,
                    )
                    break

    order = {'critical': 0, 'warning': 1}
    items.sort(
        key=lambda i: (order[i['severity']], i['scheduled_start_at'] or now)
    )
    flagged = len({i['job_id'] for i in items})
    return items, flagged


class OverviewSummaryView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary='Operations overview numbers',
        description=(
            'Counts for the selected day (`date`, `tz`): vehicle/driver '
            'availability, job status counts, resource totals and performance '
            'indicators with their numerators and denominators.'
        ),
        responses={200: None},
    )
    def get(self, request):
        org = get_user_org(request.user)
        tz, tz_name, day, start, end = selected_day(request, org)
        now = timezone.now()

        vehicles = _partition(
            Vehicle,
            rules.vehicle_usable_q(day),
            rules.committed_vehicle_ids(org, start, end),
            org,
        )
        vehicles['in_use_now'] = (
            JobInstance.objects.filter(
                organization=org, status=S.IN_PROGRESS, vehicle__isnull=False
            )
            .values('vehicle_id')
            .distinct()
            .count()
        )
        drivers = _partition(
            Driver,
            rules.driver_usable_q(day),
            rules.committed_driver_ids(org, start, end),
            org,
        )

        day_qs = rules.day_jobs(org, start, end)
        counts = {
            r['status']: r['n']
            for r in day_qs.values('status').annotate(n=Count('id'))
        }
        jobs = {
            'total': sum(counts.values()),
            'pending': counts.get(S.PENDING, 0),
            'assigned': counts.get(S.ASSIGNED, 0),
            'in_progress': counts.get(S.IN_PROGRESS, 0),
            'completed': counts.get(S.COMPLETED, 0),
            'cancelled': counts.get(S.CANCELLED, 0),
        }
        _, jobs['requiring_attention'] = build_attention(org, tz, day)

        template_counts = {
            r['status']: r['n']
            for r in JobTemplate.objects.filter(organization=org)
            .values('status')
            .annotate(n=Count('id'))
        }
        resources = {
            'depots': Depot.objects.filter(organization=org).count(),
            'fleets': Fleet.objects.filter(organization=org).count(),
            'vehicles': vehicles['total'],
            'drivers': drivers['total'],
            'routes': {
                'total': sum(template_counts.values()),
                'by_status': template_counts,
            },
            'vehicles_by_status': {
                r['status']: r['n']
                for r in Vehicle.objects.filter(organization=org)
                .values('status')
                .annotate(n=Count('id'))
            },
            'drivers_by_status': {
                r['status']: r['n']
                for r in Driver.objects.filter(organization=org)
                .values('status')
                .annotate(n=Count('id'))
            },
            'vehicles_by_powertrain': {
                'ev': Vehicle.objects.filter(
                    organization=org, fuel_type='ev'
                ).count(),
                'ice': Vehicle.objects.filter(organization=org)
                .exclude(fuel_type='ev')
                .count(),
            },
        }

        # ---- performance --------------------------------------------------
        p_start = local_day_range(
            day - timedelta(days=PERFORMANCE_DAYS - 1), tz
        )[0]
        period = JobInstance.objects.filter(
            organization=org,
            scheduled_start_at__gte=p_start,
            scheduled_start_at__lt=end,
        )
        # Completion rate: of non-cancelled jobs already due (their end, or
        # start when no end, is in the past), the share that are COMPLETED.
        due = period.exclude(status=S.CANCELLED).filter(
            Q(scheduled_end_at__lte=now)
            | Q(scheduled_end_at__isnull=True, scheduled_start_at__lte=now)
        )
        completion = _rate(due.filter(status=S.COMPLETED).count(), due.count())

        live = day_qs.exclude(status=S.CANCELLED)
        coverage = _rate(
            live.filter(vehicle__isnull=False, driver__isnull=False).count(),
            live.count(),
        )
        used = (
            live.filter(vehicle__isnull=False).values('vehicle_id').distinct()
        )
        used_usable = (
            Vehicle.objects.filter(
                organization=org, pk__in=used.values('vehicle_id')
            )
            .filter(rules.vehicle_usable_q(day))
            .count()
        )
        utilisation = _rate(used_usable, vehicles['usable'])

        return Response(
            {
                'date': day.isoformat(),
                'timezone': tz_name,
                'generated_at': now,
                'vehicles': vehicles,
                'drivers': drivers,
                'jobs': jobs,
                'resources': resources,
                'performance': {
                    'period_days': PERFORMANCE_DAYS,
                    'period_from': (
                        day - timedelta(days=PERFORMANCE_DAYS - 1)
                    ).isoformat(),
                    'period_to': day.isoformat(),
                    'completion_rate': completion,
                    'assignment_coverage': coverage,
                    'fleet_utilisation': utilisation,
                    'status_distribution': jobs,
                },
            }
        )


class AttentionView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary='Operational items needing attention',
        description=(
            'Problems derived from stored jobs for the selected day: awaiting '
            'assignment, missing vehicle/driver, assigned resource now '
            'unavailable, double bookings, late start, running over.'
        ),
        responses={200: None},
    )
    def get(self, request):
        org = get_user_org(request.user)
        tz, tz_name, day, _, _ = selected_day(request, org)
        items, flagged = build_attention(org, tz, day)
        return Response(
            {
                'date': day.isoformat(),
                'timezone': tz_name,
                'total': len(items),
                'jobs_flagged': flagged,
                'truncated': len(items) > ATTENTION_CAP,
                'items': items[:ATTENTION_CAP],
            }
        )
