"""Refreshing an external source through an adapter.

No external provider is integrated in this build: there is no official,
machine-readable API for the data EVORA needs first (Indian retail fuel
prices, state electricity tariff orders, the CEA grid emission factor), so
those values enter through cited manual entry or the CSV import, and
`ADAPTERS` is empty. This module fixes the contract any future adapter must
follow:

* an adapter is a callable `adapter(source) -> list[dict]` of observation
  fields (kind, value, unit, subject, dates, qualifiers);
* every fetched value is stored with origin "external" and stays
  *unverified* until a person verifies the source - fetching is not verifying;
* the whole refresh is atomic: if the adapter raises, or ANY returned row
  fails validation, nothing is stored;
* a failed refresh is recorded on the source (status, time, error). Values
  previously stored from that source are then reported as stale by
  `evidence.services.staleness` - never as verified current data, and never
  replaced with a hardcoded fallback.
"""

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from evidence import provenance as P
from evidence.models import (
    RefreshStatus,
    SourceVerification,
    VerificationStatus,
)
from evidence.services import create_observation

ADAPTERS = {}


class RefreshFailed(Exception):
    pass


def refresh_source(source, adapter, *, user=None):
    """Run `adapter` for `source`. Returns the created observations.

    Raises RefreshFailed (after recording the failure) on any error.
    """
    org = source.organization
    attempted = timezone.now()
    try:
        rows = adapter(source)
        if not isinstance(rows, list):
            raise RefreshFailed('Adapter did not return a list of rows.')
        with transaction.atomic():
            created = []
            for index, row in enumerate(rows):
                row = dict(row)
                row['origin'] = P.EXTERNAL
                row['source'] = source
                try:
                    created.append(create_observation(org, user, row))
                except ValidationError as exc:
                    raise RefreshFailed(f'Row {index}: {exc.detail}')
    except Exception as exc:  # noqa: BLE001 - every failure is recorded
        source.last_refresh_status = RefreshStatus.FAILED
        source.last_refresh_attempt_at = attempted
        source.last_refresh_error = str(exc)[:2000]
        source.save(update_fields=[
            'last_refresh_status', 'last_refresh_attempt_at',
            'last_refresh_error'])
        if isinstance(exc, RefreshFailed):
            raise
        raise RefreshFailed(str(exc)) from exc

    source.last_refresh_status = RefreshStatus.SUCCEEDED
    source.last_refresh_attempt_at = attempted
    source.last_successful_refresh_at = attempted
    source.last_refresh_error = ''
    # New data has not been checked by a person: verification resets.
    source.verification_status = VerificationStatus.UNVERIFIED
    source.verified_by = None
    source.verified_at = None
    source.save(update_fields=[
        'last_refresh_status', 'last_refresh_attempt_at',
        'last_successful_refresh_at', 'last_refresh_error',
        'verification_status', 'verified_by', 'verified_at'])
    SourceVerification.objects.create(
        source=source, status=VerificationStatus.UNVERIFIED, decided_by=user,
        note=f'Reset by refresh: {len(created)} new value(s) need checking.')
    return created
