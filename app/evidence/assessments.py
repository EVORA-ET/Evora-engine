"""Persisting and reproducing assessments.

Each domain registers a *compute* function per method version:

    compute(snapshot: dict, parameters: dict) -> result dict

`snapshot` is exactly what gets stored (resolved inputs + context), and the
same function runs at creation and on reproduction, so a stored result can be
re-derived from the stored snapshot alone - independent of later changes to
prices, tariffs, sources or the live database. Old method versions stay
registered so historical assessments remain reproducible after a method is
revised.
"""

import hashlib
import json

from rest_framework.exceptions import ValidationError

from evidence import provenance as P
from evidence.models import Assessment, AssessmentStatus

_REGISTRY = {}


def register(method_version, compute):
    _REGISTRY[method_version] = compute


def compute_for(method_version):
    try:
        return _REGISTRY[method_version]
    except KeyError:
        raise ValidationError(
            {'method_version': f'Method {method_version} is not available '
             'in this build.'})


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), default=str)


def fingerprint(method_version, snapshot, parameters):
    payload = canonical({'method_version': method_version,
                         'snapshot': snapshot, 'parameters': parameters})
    return hashlib.sha256(payload.encode()).hexdigest()


def inputs_of(snapshot):
    return [P.ResolvedInput.from_dict(d)
            for d in snapshot.get('inputs', {}).values()]


def run(method_version, snapshot, parameters):
    """Compute and classify. Returns (status, level, reasons, result)."""
    result = compute_for(method_version)(snapshot, parameters)
    level, reasons = P.coverage(inputs_of(snapshot))
    insufficient = (level == P.INSUFFICIENT
                    or result.get('status') == 'insufficient'
                    or result.get('outcome') == 'insufficient_evidence')
    status = (AssessmentStatus.INSUFFICIENT_EVIDENCE if insufficient
              else AssessmentStatus.COMPLETED)
    return status, level, reasons, result


def persist(*, org, user, kind, method_version, snapshot, parameters,
            vehicle=None, ev_candidate=None, subject_snapshot=None,
            warnings=None):
    status, level, reasons, result = run(method_version, snapshot, parameters)
    missing = [i.name for i in inputs_of(snapshot)
               if i.critical and not i.present]
    return Assessment.objects.create(
        organization=org, kind=kind, method_version=method_version,
        vehicle=vehicle, ev_candidate=ev_candidate,
        subject_snapshot=subject_snapshot or {},
        status=status, evidence_level=level, evidence_reasons=reasons,
        input_snapshot=snapshot, parameters=parameters, result=result,
        missing_inputs=missing, warnings=warnings or [],
        fingerprint=fingerprint(method_version, snapshot, parameters),
        created_by=user,
    )


def reproduce(assessment):
    """Recompute from the stored snapshot; report whether it matches."""
    status, level, _, result = run(
        assessment.method_version, assessment.input_snapshot,
        assessment.parameters)
    same_fp = assessment.fingerprint == fingerprint(
        assessment.method_version, assessment.input_snapshot,
        assessment.parameters)
    matches = canonical(result) == canonical(assessment.result)
    return {
        'assessment_id': str(assessment.pk),
        'method_version': assessment.method_version,
        'fingerprint_matches': same_fp,
        'result_matches': matches,
        'status_matches': status == assessment.status,
        'evidence_level_matches': level == assessment.evidence_level,
        'recomputed_result': result,
    }
