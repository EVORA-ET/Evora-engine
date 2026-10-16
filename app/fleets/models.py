import uuid

from django.contrib.gis.db import models
from django.db.models.functions import Lower


class Fleet(models.Model):
    """A named group of vehicles (e.g. "Mumbai EV fleet").

    A fleet has no depot of its own: the depots it operates from are derived
    from where its vehicles are based, so the two can never disagree.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        'organizations.Organization',
        on_delete=models.CASCADE,
        related_name='fleets',
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'fleets'
        constraints = [
            models.UniqueConstraint(
                Lower('name'),
                'organization',
                name='uniq_fleet_name_per_org',
            ),
        ]
