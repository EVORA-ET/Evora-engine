"""Business rules shared by the list filters, the assignment workflow and the
Operations overview, so every screen agrees on what "available" means.

Definitions
-----------
usable vehicle   status is `active` AND insurance (when recorded) has not
                 expired on the day in question.
usable driver    status is `active` AND licence has not expired on that day.
open job         PENDING, ASSIGNED or IN_PROGRESS. Open jobs hold their
                 vehicle and driver for their scheduled window; COMPLETED and
                 CANCELLED jobs do not.
job window       [scheduled_start_at, scheduled_end_at). When a job has no end
                 (legacy rows only - creation requires one) it is treated as an
                 instant at its start. No duration is ever invented.
"""

from datetime import timedelta

from django.db.models import Q

from drivers.models import DriverStatus
from job_instances.models import JobInstance, JobInstanceStatus
from vehicles.models import VehicleStatus

OPEN_STATUSES = (
    JobInstanceStatus.PENDING,
    JobInstanceStatus.ASSIGNED,
    JobInstanceStatus.IN_PROGRESS,
)
TERMINAL_STATUSES = (JobInstanceStatus.COMPLETED, JobInstanceStatus.CANCELLED)
# A vehicle/driver counts as "assigned" for a day when it is committed to a job
# that is fully assigned or running (not merely a half-filled pending job).
COMMITTED_STATUSES = (
    JobInstanceStatus.ASSIGNED,
    JobInstanceStatus.IN_PROGRESS,
)

VEHICLE_STATUS_TEXT = {
    VehicleStatus.INACTIVE: 'inactive',
    VehicleStatus.UNDER_MAINTENANCE: 'under maintenance',
    VehicleStatus.RETIRED: 'retired',
    VehicleStatus.SOLD: 'sold',
}
DRIVER_STATUS_TEXT = {
    DriverStatus.INACTIVE: 'inactive',
    DriverStatus.ON_LEAVE: 'on leave',
    DriverStatus.SUSPENDED: 'suspended',
    DriverStatus.TERMINATED: 'terminated',
}


# ---- usability --------------------------------------------------------


def vehicle_usable_q(on_date, prefix=''):
    return Q(**{f'{prefix}status': VehicleStatus.ACTIVE}) & (
        Q(**{f'{prefix}insurance_expiry__isnull': True})
        | Q(**{f'{prefix}insurance_expiry__gte': on_date})
    )


def driver_usable_q(on_date, prefix=''):
    return Q(**{f'{prefix}status': DriverStatus.ACTIVE}) & Q(
        **{f'{prefix}license_expiry__gte': on_date}
    )


def vehicle_blockers(vehicle, on_date):
    reasons = []
    if vehicle.status != VehicleStatus.ACTIVE:
        reasons.append(
            f'Vehicle is {VEHICLE_STATUS_TEXT.get(vehicle.status, vehicle.status)}.'
        )
    if vehicle.insurance_expiry and vehicle.insurance_expiry < on_date:
        reasons.append(
            f'Insurance expired on {vehicle.insurance_expiry:%d %b %Y}.'
        )
    return reasons


def driver_blockers(driver, on_date):
    reasons = []
    if driver.status != DriverStatus.ACTIVE:
        reasons.append(
            f'Driver is {DRIVER_STATUS_TEXT.get(driver.status, driver.status)}.'
        )
    if driver.license_expiry and driver.license_expiry < on_date:
        reasons.append(f'Licence expired on {driver.license_expiry:%d %b %Y}.')
    return reasons


# ---- time windows -----------------------------------------------------


def job_window(job):
    """(start, end) or (None, None) when the job is unscheduled."""
    start = job.scheduled_start_at
    if start is None:
        return None, None
    return start, job.scheduled_end_at or start


def windows_overlap(a_start, a_end, b_start, b_end):
    """Half-open overlap; back-to-back jobs do not conflict. A zero-length
    window (instant) conflicts only when it falls inside the other window."""
    if a_start == a_end and b_start == b_end:
        return a_start == b_start
    if a_start == a_end:
        return b_start <= a_start < b_end
    if b_start == b_end:
        return a_start <= b_start < a_end
    return a_start < b_end and b_start < a_end


def overlapping_open_jobs(org, start, end):
    """Open jobs whose window overlaps [start, end) (DB-side)."""
    if start == end:
        window = Q(
            scheduled_start_at__lte=start, scheduled_end_at__gt=start
        ) | Q(scheduled_start_at=start, scheduled_end_at__isnull=True)
    else:
        window = Q(scheduled_start_at__lt=end, scheduled_end_at__gt=start) | Q(
            scheduled_end_at__isnull=True,
            scheduled_start_at__gte=start,
            scheduled_start_at__lt=end,
        )
    return JobInstance.objects.filter(
        window,
        organization=org,
        status__in=OPEN_STATUSES,
        scheduled_start_at__isnull=False,
    )


def local_date_of(moment, tz):
    return moment.astimezone(tz).date()


def day_jobs(org, day_start, day_end):
    return JobInstance.objects.filter(
        organization=org,
        scheduled_start_at__gte=day_start,
        scheduled_start_at__lt=day_end,
    )


def committed_vehicle_ids(org, day_start, day_end):
    return set(
        day_jobs(org, day_start, day_end)
        .filter(status__in=COMMITTED_STATUSES, vehicle__isnull=False)
        .values_list('vehicle_id', flat=True)
    )


def committed_driver_ids(org, day_start, day_end):
    return set(
        day_jobs(org, day_start, day_end)
        .filter(status__in=COMMITTED_STATUSES, driver__isnull=False)
        .values_list('driver_id', flat=True)
    )


ONE_DAY = timedelta(days=1)
