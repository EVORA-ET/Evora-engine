"""Job lifecycle and assignment rules.

Everything that changes who runs a job, or what state a job is in, goes
through this module so the rules are enforced once, on the server, inside a
transaction. The HTTP layer only translates requests and errors.

Lifecycle
---------
PENDING      created, not fully assigned (it may hold a vehicle or a driver)
ASSIGNED     has BOTH a vehicle and a driver (set automatically)
IN_PROGRESS  started
COMPLETED / CANCELLED   terminal

Assignment moves a job PENDING <-> ASSIGNED automatically, so a status can
never claim an assignment the foreign keys do not back up. The status endpoint
therefore handles only: ASSIGNED->IN_PROGRESS, IN_PROGRESS->COMPLETED, and
cancelling a PENDING/ASSIGNED/IN_PROGRESS job.

Concurrency: the job row, then the vehicle row, then the driver row are locked
(`SELECT ... FOR UPDATE`) in that fixed order before the overlap check, so two
requests racing for the same vehicle serialise and the second one sees the
first one's committed assignment.
"""

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from drivers.models import Driver, DriverStatus
from job_instances.models import JobInstance, JobInstanceStatus
from job_templates.models import JobTemplateStatus
from operations import rules
from vehicles.models import Vehicle, VehicleStatus

S = JobInstanceStatus
UNSET = object()

ALLOWED_STATUS_CHANGES = {
    S.PENDING: {S.CANCELLED},
    S.ASSIGNED: {S.IN_PROGRESS, S.CANCELLED},
    S.IN_PROGRESS: {S.COMPLETED, S.CANCELLED},
    S.COMPLETED: set(),
    S.CANCELLED: set(),
}
STATUS_LABEL = {
    S.PENDING: 'pending',
    S.ASSIGNED: 'assigned',
    S.IN_PROGRESS: 'in progress',
    S.COMPLETED: 'completed',
    S.CANCELLED: 'cancelled',
}


# ---- helpers ----------------------------------------------------------


def _local(moment, tz):
    return moment.astimezone(tz)


def describe_window(job, tz):
    start, end = rules.job_window(job)
    if start is None:
        return 'unscheduled'
    s = _local(start, tz)
    if end is None or end == start:
        return f'{s:%d %b %H:%M}'
    e = _local(end, tz)
    if e.date() == s.date():
        return f'{s:%d %b %H:%M}\u2013{e:%H:%M}'
    return f'{s:%d %b %H:%M} \u2013 {e:%d %b %H:%M}'


def _locked_job(job_id, org):
    try:
        return (
            JobInstance.objects.select_for_update(of=('self',))
            .select_related('job_template')
            .get(pk=job_id, organization=org)
        )
    except JobInstance.DoesNotExist:
        raise NotFound('Job not found.')


def _lock_vehicle(vehicle_id, org):
    return (
        Vehicle.objects.select_for_update()
        .filter(pk=vehicle_id, organization=org)
        .first()
    )


def _lock_driver(driver_id, org):
    return (
        Driver.objects.select_for_update()
        .filter(pk=driver_id, organization=org)
        .first()
    )


def _conflicting_job(org, job, start, end, **resource):
    return (
        rules.overlapping_open_jobs(org, start, end)
        .exclude(pk=job.pk)
        .filter(**resource)
        .select_related('job_template')
        .order_by('scheduled_start_at')
        .first()
    )


def _check_resources(
    job, org, tz, vehicle, driver, *, check_vehicle, check_driver
):
    """Returns {field: [messages]} for the resources that are being (re)set."""
    errors = {}
    if not (check_vehicle or check_driver):
        return errors

    if job.scheduled_start_at is None or job.scheduled_end_at is None:
        errors['scheduled_start_at'] = [
            'Set a scheduled start and end for this job before assigning, '
            'so vehicle and driver conflicts can be checked.'
        ]
        return errors

    start, end = rules.job_window(job)
    on_date = rules.local_date_of(start, tz)

    if check_vehicle:
        problems = rules.vehicle_blockers(vehicle, on_date)
        clash = _conflicting_job(org, job, start, end, vehicle_id=vehicle.pk)
        if clash:
            problems.append(
                f'{vehicle.registration_number} is already booked on '
                f'\u201c{clash.job_template.name}\u201d ({describe_window(clash, tz)}).'
            )
        if problems:
            errors['vehicle_id'] = problems

    if check_driver:
        problems = rules.driver_blockers(driver, on_date)
        clash = _conflicting_job(org, job, start, end, driver_id=driver.pk)
        if clash:
            problems.append(
                f'{driver.name} is already booked on '
                f'\u201c{clash.job_template.name}\u201d ({describe_window(clash, tz)}).'
            )
        if problems:
            errors['driver_id'] = problems

    return errors


def _apply_assignment(job, org, tz, vehicle_id, driver_id):
    """Validate and persist vehicle/driver on a *locked* job. Caller owns the
    transaction."""
    if job.status not in (S.PENDING, S.ASSIGNED):
        raise ValidationError(
            {
                'status': f'A {STATUS_LABEL[job.status]} job can no longer be (re)assigned.'
            }
        )

    new_vehicle_id = job.vehicle_id if vehicle_id is UNSET else vehicle_id
    new_driver_id = job.driver_id if driver_id is UNSET else driver_id

    errors = {}
    vehicle = driver = None
    # Fixed lock order: vehicle, then driver.
    if new_vehicle_id:
        vehicle = _lock_vehicle(new_vehicle_id, org)
        if vehicle is None:
            errors['vehicle_id'] = ['Vehicle not found.']
    if new_driver_id:
        driver = _lock_driver(new_driver_id, org)
        if driver is None:
            errors['driver_id'] = ['Driver not found.']
    if errors:
        raise ValidationError(errors)

    # Only the resources being set/changed are re-validated. A resource that
    # was valid when assigned and has since gone bad is reported by the
    # attention list instead of blocking an unrelated change.
    errors = _check_resources(
        job,
        org,
        tz,
        vehicle,
        driver,
        check_vehicle=bool(vehicle) and new_vehicle_id != job.vehicle_id,
        check_driver=bool(driver) and new_driver_id != job.driver_id,
    )
    if errors:
        raise ValidationError(errors)

    job.vehicle_id = new_vehicle_id
    job.driver_id = new_driver_id
    job.status = (
        S.ASSIGNED if (new_vehicle_id and new_driver_id) else S.PENDING
    )
    job.save(update_fields=['vehicle', 'driver', 'status', 'updated_at'])
    return job


# ---- public operations --------------------------------------------------


def assign_job(*, job_id, org, tz, vehicle_id=UNSET, driver_id=UNSET):
    with transaction.atomic():
        job = _locked_job(job_id, org)
        _apply_assignment(job, org, tz, vehicle_id, driver_id)
    return JobInstance.objects.get(pk=job.pk)


def create_job(
    *, org, tz, template, start, end, vehicle_id=None, driver_id=None
):
    if template.status != JobTemplateStatus.ACTIVE:
        raise ValidationError(
            {
                'job_template_id': f'This route is {template.status.lower()}; only active routes can be scheduled.'
            }
        )
    if end is None and template.estimated_duration_minutes:
        from datetime import timedelta

        end = start + timedelta(minutes=template.estimated_duration_minutes)
    if end is None:
        raise ValidationError(
            {
                'scheduled_end_at': 'Provide an end time (this route has no estimated duration to derive one from).'
            }
        )
    if start >= end:
        raise ValidationError(
            {'scheduled_end_at': 'End must be after the start.'}
        )

    day = rules.local_date_of(start, tz)
    if template.start_date and day < template.start_date:
        raise ValidationError(
            {
                'scheduled_start_at': f'This route starts on {template.start_date:%d %b %Y}.'
            }
        )
    if template.end_date and day > template.end_date:
        raise ValidationError(
            {
                'scheduled_start_at': f'This route ended on {template.end_date:%d %b %Y}.'
            }
        )

    with transaction.atomic():
        job = JobInstance.objects.create(
            organization=org,
            job_template=template,
            scheduled_start_at=start,
            scheduled_end_at=end,
            status=S.PENDING,
        )
        if vehicle_id or driver_id:
            job = (
                JobInstance.objects.select_for_update(of=('self',))
                .select_related('job_template')
                .get(pk=job.pk)
            )
            _apply_assignment(
                job,
                org,
                tz,
                vehicle_id if vehicle_id else UNSET,
                driver_id if driver_id else UNSET,
            )
    return JobInstance.objects.get(pk=job.pk)


def update_schedule(
    *, job_id, org, tz, start=UNSET, end=UNSET, template=UNSET
):
    """Change a job's schedule/route; re-check conflicts if it is assigned."""
    with transaction.atomic():
        job = _locked_job(job_id, org)
        if job.status not in (S.PENDING, S.ASSIGNED):
            raise ValidationError(
                {
                    'status': f'A {STATUS_LABEL[job.status]} job can no longer be rescheduled.'
                }
            )
        if template is not UNSET and template.pk != job.job_template_id:
            if job.status != S.PENDING:
                raise ValidationError(
                    {
                        'job_template_id': 'Unassign the job before changing its route.'
                    }
                )
            job.job_template = template
        if start is not UNSET:
            job.scheduled_start_at = start
        if end is not UNSET:
            job.scheduled_end_at = end
        if (
            job.scheduled_start_at
            and job.scheduled_end_at
            and (job.scheduled_start_at >= job.scheduled_end_at)
        ):
            raise ValidationError(
                {'scheduled_end_at': 'End must be after the start.'}
            )

        if job.status == S.ASSIGNED:
            if job.scheduled_start_at is None or job.scheduled_end_at is None:
                raise ValidationError(
                    {
                        'scheduled_start_at': 'An assigned job needs a start and an end.'
                    }
                )
            vehicle = (
                _lock_vehicle(job.vehicle_id, org) if job.vehicle_id else None
            )
            driver = (
                _lock_driver(job.driver_id, org) if job.driver_id else None
            )
            errors = _check_resources(
                job,
                org,
                tz,
                vehicle,
                driver,
                check_vehicle=bool(vehicle),
                check_driver=bool(driver),
            )
            if errors:
                raise ValidationError(errors)

        job.save()
    return JobInstance.objects.get(pk=job.pk)


def change_status(*, job_id, org, tz, new_status, now=None):
    now = now or timezone.now()
    with transaction.atomic():
        job = _locked_job(job_id, org)

        if new_status == job.status:
            raise ValidationError(
                {'status': f'Job is already {STATUS_LABEL[job.status]}.'}
            )
        if new_status in (S.ASSIGNED, S.PENDING):
            raise ValidationError(
                {
                    'status': 'Assigned/pending follow from the vehicle and driver; '
                    'use the assignment workflow instead.'
                }
            )
        allowed = ALLOWED_STATUS_CHANGES[job.status]
        if new_status not in allowed:
            options = (
                ', '.join(sorted(STATUS_LABEL[s] for s in allowed))
                or 'none (final state)'
            )
            raise ValidationError(
                {
                    'status': f'A {STATUS_LABEL[job.status]} job cannot become '
                    f'{STATUS_LABEL[new_status]}. Allowed next: {options}.'
                }
            )

        if new_status == S.IN_PROGRESS:
            if not (job.vehicle_id and job.driver_id):
                raise ValidationError(
                    {
                        'status': 'Assign a vehicle and a driver before starting.'
                    }
                )
            vehicle = _lock_vehicle(job.vehicle_id, org)
            driver = _lock_driver(job.driver_id, org)
            today = rules.local_date_of(now, tz)
            problems = rules.vehicle_blockers(
                vehicle, today
            ) + rules.driver_blockers(driver, today)
            running = JobInstance.objects.filter(
                organization=org, status=S.IN_PROGRESS
            ).exclude(pk=job.pk)
            if running.filter(vehicle_id=vehicle.pk).exists():
                problems.append(
                    f'{vehicle.registration_number} is already running another job.'
                )
            if running.filter(driver_id=driver.pk).exists():
                problems.append(
                    f'{driver.name} is already running another job.'
                )
            if problems:
                raise ValidationError({'status': problems})
            job.actual_start_at = now
        elif new_status == S.COMPLETED:
            job.actual_end_at = now
            job.completion_percentage = 100
        elif new_status == S.CANCELLED:
            if job.actual_start_at and not job.actual_end_at:
                job.actual_end_at = now

        job.status = new_status
        job.save()
    return JobInstance.objects.get(pk=job.pk)


def eligible_resources(*, job, org, tz, search='', limit=100):
    """Vehicles and drivers for the assignment drawer, each flagged eligible or
    not with the reasons. Eligible first; vehicles based at the route's source
    depot sort ahead of others."""
    start, end = rules.job_window(job)
    scheduled = (
        job.scheduled_start_at is not None and job.scheduled_end_at is not None
    )
    template = job.job_template

    busy_vehicle, busy_driver = {}, {}
    on_date = None
    if scheduled:
        on_date = rules.local_date_of(start, tz)
        others = (
            rules.overlapping_open_jobs(org, start, end)
            .exclude(pk=job.pk)
            .select_related('job_template')
            .order_by('scheduled_start_at')
        )
        for other in others:
            if other.vehicle_id:
                busy_vehicle.setdefault(other.vehicle_id, other)
            if other.driver_id:
                busy_driver.setdefault(other.driver_id, other)

    vehicles = (
        Vehicle.objects.filter(organization=org)
        .exclude(status__in=[VehicleStatus.RETIRED, VehicleStatus.SOLD])
        .select_related('depot', 'fleet')
    )
    drivers = Driver.objects.filter(organization=org).exclude(
        status=DriverStatus.TERMINATED
    )
    if search:
        from django.db.models import Q

        vehicles = vehicles.filter(
            Q(registration_number__icontains=search)
            | Q(manufacturer__icontains=search)
            | Q(model__icontains=search)
        )
        drivers = drivers.filter(
            Q(name__icontains=search) | Q(driver_code__icontains=search)
        )

    vehicle_rows = []
    for v in vehicles.order_by('registration_number')[: limit * 3]:
        reasons = []
        if not scheduled:
            reasons.append(
                'Schedule the job (start and end) before assigning.'
            )
        else:
            reasons += rules.vehicle_blockers(v, on_date)
            clash = busy_vehicle.get(v.pk)
            if clash:
                reasons.append(
                    f'Already booked on \u201c{clash.job_template.name}\u201d ({describe_window(clash, tz)}).'
                )
        warnings = []
        if (
            v.fuel_type == 'ev'
            and v.battery_capacity_kwh is not None
            and template.estimated_energy_kwh is not None
            and template.estimated_energy_kwh > v.battery_capacity_kwh
        ):
            warnings.append(
                f'Route needs about {template.estimated_energy_kwh} kWh; '
                f'battery is {v.battery_capacity_kwh} kWh.'
            )
        vehicle_rows.append(
            {
                'id': v.pk,
                'registration_number': v.registration_number,
                'manufacturer': v.manufacturer,
                'model': v.model,
                'vehicle_type': v.vehicle_type,
                'fuel_type': v.fuel_type,
                'status': v.status,
                'depot_id': v.depot_id,
                'depot_name': v.depot.name if v.depot else None,
                'fleet_name': v.fleet.name if v.fleet else None,
                'battery_capacity_kwh': v.battery_capacity_kwh,
                'at_source_depot': v.depot_id is not None
                and v.depot_id == template.source_depot_id,
                'eligible': not reasons,
                'reasons': reasons,
                'warnings': warnings,
            }
        )

    driver_rows = []
    for d in drivers.order_by('name')[: limit * 3]:
        reasons = []
        if not scheduled:
            reasons.append(
                'Schedule the job (start and end) before assigning.'
            )
        else:
            reasons += rules.driver_blockers(d, on_date)
            clash = busy_driver.get(d.pk)
            if clash:
                reasons.append(
                    f'Already booked on \u201c{clash.job_template.name}\u201d ({describe_window(clash, tz)}).'
                )
        driver_rows.append(
            {
                'id': d.pk,
                'driver_code': d.driver_code,
                'name': d.name,
                'status': d.status,
                'shift': d.shift,
                'license_class': d.license_class,
                'license_expiry': d.license_expiry,
                'experience_years': d.experience_years,
                'eligible': not reasons,
                'reasons': reasons,
                'warnings': [],
            }
        )

    vehicle_rows.sort(
        key=lambda r: (
            not r['eligible'],
            not r['at_source_depot'],
            r['registration_number'],
        )
    )
    driver_rows.sort(key=lambda r: (not r['eligible'], r['name'].lower()))
    return {
        'scheduled': scheduled,
        'window_start': job.scheduled_start_at,
        'window_end': job.scheduled_end_at,
        'vehicles': vehicle_rows[:limit],
        'drivers': driver_rows[:limit],
        'vehicle_total': len(vehicle_rows),
        'driver_total': len(driver_rows),
    }


# ---- helpers for list views ---------------------------------------------


def open_jobs_by(field, ids, org):
    """{resource_id: [open jobs ordered by start]} for a page of vehicles/drivers."""
    jobs = (
        JobInstance.objects.filter(
            organization=org,
            status__in=rules.OPEN_STATUSES,
            **{f'{field}__in': ids},
        )
        .select_related('job_template')
        .order_by('scheduled_start_at')
    )
    grouped = {}
    for job in jobs:
        grouped.setdefault(getattr(job, f'{field}_id'), []).append(job)
    return grouped


def summarise_current_job(jobs):
    """The running job if any, else the next scheduled one."""
    if not jobs:
        return None
    running = [j for j in jobs if j.status == S.IN_PROGRESS]
    chosen = running[0] if running else jobs[0]
    return {
        'id': chosen.pk,
        'status': chosen.status,
        'job_template_id': chosen.job_template_id,
        'job_template_name': chosen.job_template.name,
        'scheduled_start_at': chosen.scheduled_start_at,
        'scheduled_end_at': chosen.scheduled_end_at,
    }
