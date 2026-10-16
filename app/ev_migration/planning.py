"""Migration planning: validation, derived flags and status changes.

Flags are read from the linked, immutable assessments and decision; nothing
is recalculated. Status only changes through `change_status`, which enforces
PLAN_ITEM_TRANSITIONS and records every change:

* in_progress requires a linked `approve_transition` decision (which itself
  required a readiness assessment that met every check with moderate/high
  evidence coverage);
* completed is only reachable from in_progress and requires the completion
  date and a note - it is never inferred from an approval.
"""

from django.db import transaction
from rest_framework.exceptions import ValidationError

from ev_migration.models import (
    PLAN_ITEM_TRANSITIONS,
    MigrationPlanItem,
    PlanItemStatus,
    PlanItemStatusChange,
)
from evidence import provenance as P
from evidence.models import AssessmentKind, AssessmentStatus
from evidence.services import today_for

S = PlanItemStatus

FLAG_TEXT = {
    'no_readiness': 'No readiness assessment linked.',
    'readiness_failed': 'Readiness: a mandatory check failed.',
    'readiness_insufficient': 'Readiness: evidence insufficient to decide.',
    'low_evidence': 'Readiness evidence coverage is low or insufficient.',
    'no_tco': 'No TCO assessment linked.',
    'tco_insufficient': 'TCO could not be calculated from the evidence.',
    'no_candidate': 'No EV model selected.',
    'no_approval': 'No formal approval decision linked.',
    'not_approved': 'Linked decision is not an approval.',
}


def item_flags(item):
    flags = []
    if item.ev_candidate_id is None:
        flags.append('no_candidate')
    r = item.readiness_assessment
    if r is None:
        flags.append('no_readiness')
    else:
        outcome = (r.result or {}).get('outcome')
        if outcome == 'not_suitable':
            flags.append('readiness_failed')
        elif outcome != 'meets_requirements':
            flags.append('readiness_insufficient')
        if r.evidence_level in (P.LOW, P.INSUFFICIENT):
            flags.append('low_evidence')
    t = item.tco_assessment
    if t is None:
        flags.append('no_tco')
    elif t.status != AssessmentStatus.COMPLETED:
        flags.append('tco_insufficient')
    d = item.decision
    if d is None:
        flags.append('no_approval')
    elif d.decision != 'approve_transition':
        flags.append('not_approved')
    return [{'code': f, 'text': FLAG_TEXT[f]} for f in flags]


def validate_links(attrs, *, vehicle, org, instance=None):
    """Assessments/decision must belong to the item's vehicle (and candidate).
    Returns field -> message errors."""
    errors = {}
    cand = attrs.get('ev_candidate', getattr(instance, 'ev_candidate', None))
    if vehicle.fuel_type == 'ev':
        errors['vehicle_id'] = 'This vehicle is already electric.'
    for field, kind in (('readiness_assessment', AssessmentKind.EV_READINESS),
                        ('tco_assessment', AssessmentKind.TCO)):
        a = attrs.get(field)
        if a is None:
            continue
        if a.kind != kind:
            errors[f'{field}_id'] = f'Must be a {kind} assessment.'
        elif a.vehicle_id != vehicle.pk:
            errors[f'{field}_id'] = 'Assessment is for another vehicle.'
        elif cand is not None and a.ev_candidate_id != cand.pk:
            errors[f'{field}_id'] = 'Assessment is for another EV candidate.'
    d = attrs.get('decision')
    if d is not None:
        if d.vehicle_id != vehicle.pk:
            errors['decision_id'] = 'Decision is for another vehicle.'
        elif cand is not None and d.ev_candidate_id not in (None, cand.pk):
            errors['decision_id'] = 'Decision is for another EV candidate.'
    return errors


def change_status(*, item_id, org, user, to_status, note, completed_on=None):
    if to_status not in S.values:
        raise ValidationError({'status': f'Use one of: {", ".join(S.values)}.'})
    with transaction.atomic():
        item = (MigrationPlanItem.objects.select_for_update(of=('self',))
                .select_related('decision', 'plan')
                .filter(pk=item_id, plan__organization=org).first())
        if item is None:
            from rest_framework.exceptions import NotFound
            raise NotFound('Plan item not found.')
        if to_status == item.status:
            raise ValidationError({'status': f'Already {item.status}.'})
        allowed = PLAN_ITEM_TRANSITIONS[S(item.status)]
        if to_status not in allowed:
            nxt = ', '.join(sorted(allowed)) or 'none (final state)'
            raise ValidationError({'status': (
                f'A {item.status} item cannot become {to_status}. Allowed '
                f'next: {nxt}.')})
        if to_status == S.IN_PROGRESS and (
                item.decision is None
                or item.decision.decision != 'approve_transition'):
            raise ValidationError({'status': (
                'Starting a transition needs a linked formal approval '
                'decision (approve_transition). Record the decision under '
                'Tracking and link it to this item first.')})
        if to_status == S.COMPLETED:
            if completed_on is None:
                raise ValidationError({'completed_on': (
                    'Give the date the EV actually entered service.')})
            if completed_on > today_for(org)[0]:
                raise ValidationError(
                    {'completed_on': 'Cannot be in the future.'})
            if not (note or '').strip():
                raise ValidationError({'note': (
                    'Describe how completion was confirmed (e.g. registration '
                    'or delivery record).')})
            item.completed_on = completed_on
        elif item.completed_on is not None:
            item.completed_on = None
        PlanItemStatusChange.objects.create(
            item=item, from_status=item.status, to_status=to_status,
            note=note or '', changed_by=user)
        item.status = to_status
        item.save(update_fields=['status', 'completed_on', 'updated_at'])
    return item
