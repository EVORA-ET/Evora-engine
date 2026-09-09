import uuid

from django.contrib.gis.db import models
from django.contrib.postgres.fields import ArrayField


class Organization(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    industry = models.CharField(max_length=255, blank=True, default='')
    country = models.CharField(max_length=100, blank=True, default='')
    headquarters_address = models.TextField(blank=True, default='')
    headquarters_location = models.PointField(
        srid=4326, geography=True, null=True, blank=True
    )
    operating_states = ArrayField(
        models.CharField(max_length=100), blank=True, default=list
    )
    operating_cities = ArrayField(
        models.CharField(max_length=255), blank=True, default=list
    )
    gst_number = models.CharField(max_length=50, blank=True, default='')
    website = models.URLField(max_length=500, blank=True, default='')
    logo_url = models.URLField(max_length=500, blank=True, default='')
    timezone = models.CharField(max_length=100, default='UTC')
    currency = models.CharField(max_length=10, default='INR')
    business_hours = models.JSONField(default=dict, blank=True)
    data_quality_score = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'organizations'
