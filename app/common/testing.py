"""Fixtures shared by the API tests (kept out of the app code paths)."""

import itertools
from datetime import date, datetime, timedelta, timezone as dt_tz

from rest_framework.test import APIClient

from depots.models import Depot
from drivers.models import Driver
from job_instances.models import JobInstance
from job_templates.models import JobTemplate
from organizations.models import Organization
from users.models import User
from vehicles.models import Vehicle

_n = itertools.count(1)
UTC = dt_tz.utc


def make_org(name='Acme Logistics'):
    return Organization.objects.create(name=name)


def make_user(org, email=None):
    n = next(_n)
    return User.objects.create(
        email=email or f'user{n}@example.com',
        name=f'User {n}',
        organization=org,
    )


def client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def make_depot(org, name=None, **kw):
    n = next(_n)
    return Depot.objects.create(
        organization=org,
        name=name or f'Depot {n}',
        address=f'{n} Main Road',
        **kw,
    )


def make_vehicle(org, reg=None, **kw):
    n = next(_n)
    defaults = dict(
        registration_number=reg or f'MH12AB{1000 + n}',
        manufacturer='Tata',
        model='Ace',
        manufacture_year=2022,
        purchase_date=date(2022, 1, 1),
        vehicle_type='truck',
        fuel_type='diesel',
        mileage_kmpl='12.5',
        payload_kg='1000',
        seating_capacity=2,
        gvw_kg='2500',
    )
    defaults.update(kw)
    return Vehicle.objects.create(organization=org, **defaults)


def make_driver(org, name=None, **kw):
    n = next(_n)
    defaults = dict(
        driver_code=f'DRV{n:04d}',
        name=name or f'Driver {n}',
        date_of_birth=date(1990, 1, 1),
        license_number=f'LIC{n:06d}',
        license_class='HMV',
        license_expiry=date.today() + timedelta(days=700),
        experience_years='5.0',
    )
    defaults.update(kw)
    return Driver.objects.create(organization=org, **defaults)


def make_template(org, source, destination=None, name=None, **kw):
    n = next(_n)
    return JobTemplate.objects.create(
        organization=org,
        source_depot=source,
        destination_depot=destination,
        name=name or f'Route {n}',
        **kw,
    )


def at(hour, minute=0, days=1):
    """A fixed future UTC instant: tomorrow-ish at hour:minute."""
    base = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return base + timedelta(days=days, hours=hour, minutes=minute)


def make_job(
    org,
    template,
    start=None,
    end=None,
    vehicle=None,
    driver=None,
    status='PENDING',
):
    start = start or at(9)
    end = end or at(11)
    return JobInstance.objects.create(
        organization=org,
        job_template=template,
        vehicle=vehicle,
        driver=driver,
        scheduled_start_at=start,
        scheduled_end_at=end,
        status=status,
    )


class World:
    """One organization with a depot pair, a route, a vehicle and a driver."""

    def __init__(self):
        self.org = make_org()
        self.user = make_user(self.org)
        self.client = client_for(self.user)
        self.depot_a = make_depot(self.org, 'Depot A')
        self.depot_b = make_depot(self.org, 'Depot B')
        self.template = make_template(
            self.org, self.depot_a, self.depot_b, name='A to B'
        )
        self.vehicle = make_vehicle(self.org, 'MH12AB0001')
        self.driver = make_driver(self.org, 'Asha Rao')
