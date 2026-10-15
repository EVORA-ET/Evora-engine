"""Shared evidence store for EV Migration and Health & Maintenance.

Nothing here duplicates the Operations tables: observations point at the
existing Vehicle and Depot rows. Evidence is append-only - an observation is
corrected by recording a new one that `supersedes` it, and records are voided
rather than deleted - so a past assessment can always be traced to the values
that informed it.
"""

import uuid

from django.db import models

from evidence import provenance as P
from evidence.kinds import KINDS

ORIGIN_CHOICES = [(o, o) for o in P.ORIGINS]


class ImmutableModel(models.Model):
    """Rows that may be created but never edited in place.

    `mutable_fields` lists the only columns a later save may touch (e.g. the
    void markers); anything else raises.
    """

    mutable_fields = ()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            update_fields = kwargs.get('update_fields')
            if not update_fields or not set(update_fields) <= set(
                    self.mutable_fields):
                raise ValueError(
                    f'{type(self).__name__} rows are immutable; record a '
                    'correction instead.')
        super().save(*args, **kwargs)


class SourceType(models.TextChoices):
    MANUFACTURER_DOCUMENT = 'manufacturer_document'
    PROCUREMENT_QUOTATION = 'procurement_quotation'
    INVOICE = 'invoice'
    UTILITY_BILL = 'utility_bill'
    TARIFF_ORDER = 'tariff_order'
    FUEL_PRICE_NOTICE = 'fuel_price_notice'
    GOVERNMENT_NOTIFICATION = 'government_notification'
    EMISSION_FACTOR_PUBLICATION = 'emission_factor_publication'
    SERVICE_RECORD = 'service_record'
    TELEMATICS_EXPORT = 'telematics_export'
    FUEL_CARD_EXPORT = 'fuel_card_export'
    CHARGER_LOG = 'charger_log'
    INTERNAL_RECORD = 'internal_record'
    OTHER = 'other'


class RetrievalMethod(models.TextChoices):
    MANUAL_ENTRY = 'manual_entry'
    DOCUMENT = 'document'
    FILE_IMPORT = 'file_import'
    API = 'api'


class VerificationStatus(models.TextChoices):
    UNVERIFIED = 'unverified'
    VERIFIED = 'verified'
    REJECTED = 'rejected'


class RefreshStatus(models.TextChoices):
    NOT_APPLICABLE = 'not_applicable'
    SUCCEEDED = 'succeeded'
    FAILED = 'failed'


class EvidenceSource(models.Model):
    """Where a value came from: a document, record export, quotation or API.

    Only the verification and refresh bookkeeping can change after creation.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        'organizations.Organization', on_delete=models.CASCADE,
        related_name='evidence_sources')
    title = models.CharField(max_length=255)
    source_type = models.CharField(max_length=50, choices=SourceType.choices)
    publisher = models.CharField(max_length=255, blank=True, default='')
    identifier = models.CharField(
        max_length=255, blank=True, default='',
        help_text='Document number, quotation reference, notification id.')
    url = models.URLField(max_length=1000, blank=True, default='')
    retrieval_method = models.CharField(
        max_length=20, choices=RetrievalMethod.choices)
    retrieved_at = models.DateTimeField()
    published_on = models.DateField(null=True, blank=True)
    effective_from = models.DateField(null=True, blank=True)
    effective_to = models.DateField(null=True, blank=True)
    geographic_scope = models.CharField(max_length=255, blank=True, default='')
    applicability = models.TextField(
        blank=True, default='',
        help_text='Vehicle categories / conditions the source applies to.')
    methodology = models.TextField(blank=True, default='')
    notes = models.TextField(blank=True, default='')

    verification_status = models.CharField(
        max_length=20, choices=VerificationStatus.choices,
        default=VerificationStatus.UNVERIFIED)
    verified_by = models.ForeignKey(
        'users.User', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='+')
    verified_at = models.DateTimeField(null=True, blank=True)
    verification_note = models.TextField(blank=True, default='')

    refresh_interval_days = models.IntegerField(null=True, blank=True)
    last_refresh_status = models.CharField(
        max_length=20, choices=RefreshStatus.choices,
        default=RefreshStatus.NOT_APPLICABLE)
    last_refresh_attempt_at = models.DateTimeField(null=True, blank=True)
    last_successful_refresh_at = models.DateTimeField(null=True, blank=True)
    last_refresh_error = models.TextField(blank=True, default='')

    created_by = models.ForeignKey(
        'users.User', null=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'evidence_sources'
        ordering = ['-created_at']

    @property
    def is_verified(self):
        return self.verification_status == VerificationStatus.VERIFIED

    def as_reference(self):
        return {
            'id': str(self.pk),
            'title': self.title,
            'source_type': self.source_type,
            'publisher': self.publisher,
            'identifier': self.identifier,
            'url': self.url,
            'retrieval_method': self.retrieval_method,
            'retrieved_at': self.retrieved_at.isoformat(),
            'verification_status': self.verification_status,
            'verified_at': (self.verified_at.isoformat()
                            if self.verified_at else None),
            'last_refresh_status': self.last_refresh_status,
        }


class ValidationStatus(models.TextChoices):
    VALID = 'valid'
    FLAGGED = 'flagged'


class Observation(ImmutableModel):
    """One typed, dated, sourced value about a vehicle, depot, EV candidate or
    the organization. See `evidence.kinds` for the meaning of each kind."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        'organizations.Organization', on_delete=models.CASCADE,
        related_name='observations')
    kind = models.CharField(
        max_length=64, choices=[(k, k) for k in KINDS])
    value = models.DecimalField(max_digits=20, decimal_places=6)
    unit = models.CharField(max_length=32)

    vehicle = models.ForeignKey(
        'vehicles.Vehicle', null=True, blank=True, on_delete=models.CASCADE,
        related_name='observations')
    depot = models.ForeignKey(
        'depots.Depot', null=True, blank=True, on_delete=models.CASCADE,
        related_name='observations')
    ev_candidate = models.ForeignKey(
        'ev_migration.EVCandidate', null=True, blank=True,
        on_delete=models.CASCADE, related_name='observations')
    qualifiers = models.JSONField(default=dict, blank=True)

    origin = models.CharField(max_length=32, choices=ORIGIN_CHOICES)
    source = models.ForeignKey(
        EvidenceSource, null=True, blank=True, on_delete=models.PROTECT,
        related_name='observations')

    observed_at = models.DateTimeField(null=True, blank=True)
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    effective_from = models.DateField(null=True, blank=True)
    effective_to = models.DateField(null=True, blank=True)

    validation_status = models.CharField(
        max_length=20, choices=ValidationStatus.choices,
        default=ValidationStatus.VALID)
    validation_notes = models.JSONField(default=list, blank=True)
    transformation = models.TextField(
        blank=True, default='',
        help_text='How the value was derived, when it was not read directly.')
    note = models.TextField(blank=True, default='')

    supersedes = models.OneToOneField(
        'self', null=True, blank=True, on_delete=models.PROTECT,
        related_name='superseded_by')
    correction_reason = models.TextField(blank=True, default='')

    created_by = models.ForeignKey(
        'users.User', null=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'evidence_observations'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['organization', 'kind']),
            models.Index(fields=['vehicle', 'kind', 'observed_at']),
        ]

    @property
    def subject(self):
        if self.vehicle_id:
            return 'vehicle'
        if self.depot_id:
            return 'depot'
        if self.ev_candidate_id:
            return 'ev_candidate'
        return 'organization'


class EnergyRecordType(models.TextChoices):
    FUEL_FILL = 'fuel_fill'
    CHARGING_SESSION = 'charging_session'


class EnergyBasis(models.TextChoices):
    GRID = 'grid'        # metered at the charger / supply point
    VEHICLE = 'vehicle'  # reported by the vehicle (energy into the battery)


class EnergyRecord(ImmutableModel):
    """A fuel fill or a charging session, as recorded by the organization.

    `is_full` means the tank was filled to full / the battery charged to full;
    fill-to-fill consumption is derived only between two full records.
    """

    mutable_fields = ('voided_at', 'void_reason', 'voided_by')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        'organizations.Organization', on_delete=models.CASCADE,
        related_name='energy_records')
    vehicle = models.ForeignKey(
        'vehicles.Vehicle', on_delete=models.CASCADE,
        related_name='energy_records')
    record_type = models.CharField(
        max_length=20, choices=EnergyRecordType.choices)
    occurred_at = models.DateTimeField()
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    energy_basis = models.CharField(
        max_length=10, choices=EnergyBasis.choices, blank=True, default='')
    is_full = models.BooleanField()
    odometer_km = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True)
    cost = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=10, blank=True, default='')
    depot = models.ForeignKey(
        'depots.Depot', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='+')
    origin = models.CharField(max_length=32, choices=ORIGIN_CHOICES)
    source = models.ForeignKey(
        EvidenceSource, null=True, blank=True, on_delete=models.PROTECT,
        related_name='energy_records')
    note = models.TextField(blank=True, default='')

    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.TextField(blank=True, default='')
    voided_by = models.ForeignKey(
        'users.User', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='+')
    created_by = models.ForeignKey(
        'users.User', null=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'evidence_energy_records'
        ordering = ['occurred_at']
        indexes = [models.Index(fields=['vehicle', 'occurred_at'])]

    @property
    def unit(self):
        return 'L' if self.record_type == EnergyRecordType.FUEL_FILL else 'kWh'


class AssessmentKind(models.TextChoices):
    EV_READINESS = 'ev_readiness'
    TCO = 'tco'
    EMISSIONS = 'emissions'
    MAINTENANCE_STATUS = 'maintenance_status'


class AssessmentStatus(models.TextChoices):
    COMPLETED = 'completed'
    INSUFFICIENT_EVIDENCE = 'insufficient_evidence'


class Assessment(ImmutableModel):
    """An immutable, reproducible record of one calculation.

    `input_snapshot` holds every resolved input (value, unit, origin, evidence
    class, source reference, dates) and `parameters` the decision choices.
    Re-running `method_version` on them must reproduce `result` exactly; the
    fingerprint makes that checkable.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        'organizations.Organization', on_delete=models.CASCADE,
        related_name='assessments')
    kind = models.CharField(max_length=32, choices=AssessmentKind.choices)
    method_version = models.CharField(max_length=64)
    vehicle = models.ForeignKey(
        'vehicles.Vehicle', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='assessments')
    ev_candidate = models.ForeignKey(
        'ev_migration.EVCandidate', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='assessments')
    subject_snapshot = models.JSONField(
        default=dict,
        help_text='Identity of the vehicle/candidate at assessment time.')
    status = models.CharField(max_length=32, choices=AssessmentStatus.choices)
    evidence_level = models.CharField(
        max_length=20, choices=[(c, c) for c in P.COVERAGE_LEVELS])
    evidence_reasons = models.JSONField(default=list)
    input_snapshot = models.JSONField(default=dict)
    parameters = models.JSONField(default=dict)
    result = models.JSONField(default=dict)
    missing_inputs = models.JSONField(default=list)
    warnings = models.JSONField(default=list)
    fingerprint = models.CharField(max_length=64)
    created_by = models.ForeignKey(
        'users.User', null=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'assessments'
        ordering = ['-created_at']
        indexes = [models.Index(fields=['organization', 'kind', 'vehicle'])]
