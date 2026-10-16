"""API exception handling for protected evidence.

Evidence tables reference vehicles, depots, EV candidates and sources with
`on_delete=RESTRICT`, so a record that has evidence, maintenance history
or assessments cannot be hard-deleted on its own. RESTRICT (unlike PROTECT)
still lets a whole organization be deleted, because its evidence is
cascade-deleted with it. Without this handler Django's `RestrictedError`
would surface as a 500 from the existing delete endpoints; it becomes a 409
in the shape the Operations views already use
(`{"detail": ..., "blockers": {...}}`).

Before these apps existed nothing in EVORA used PROTECT or RESTRICT, so no
existing endpoint changes behaviour: a delete that used to succeed still succeeds
unless the record now has evidence attached.
"""

from django.db.models import ProtectedError, RestrictedError
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler


def exception_handler(exc, context):
    if isinstance(exc, (ProtectedError, RestrictedError)):
        blockers = {}
        objs = (exc.protected_objects if isinstance(exc, ProtectedError)
                else exc.restricted_objects)
        for obj in objs:
            key = obj._meta.db_table
            blockers[key] = blockers.get(key, 0) + 1
        listing = ', '.join(f'{n} {k.replace("_", " ")}'
                            for k, n in sorted(blockers.items()))
        return Response(
            {'detail': ('This record is referenced by stored evidence or '
                        f'assessments ({listing}). Deleting it would erase '
                        'audit history. Retire, sell or archive it instead.'),
             'blockers': blockers},
            status=status.HTTP_409_CONFLICT)
    return drf_exception_handler(exc, context)
