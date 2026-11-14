"""Explicit split routing by persistent accounting capability, never task phase.

Memory work stays on its route even when started by a Hermes retrieval tool or
when a native background worker completes after the invoking turn.
"""
import os
from .openrouter_route import PROFILES, PROFILE_NAME

CONDITION = 'luna-memory-deepseek-v1'


def split_enabled():
    condition = os.environ.get('PACBENCH_ROLE_ROUTES')
    if not condition:
        return False
    if condition != CONDITION or PROFILE_NAME != 'deepseek-flex':
        raise ValueError('Split roles require the explicit condition and DeepSeek driver profile')
    return True


def profile_for_project(project):
    if not split_enabled():
        return PROFILE_NAME
    if project.endswith('-memory'):
        return 'luna-bedrock-global'
    if project.endswith('-driver') or project == 'pacbench-judge':
        return 'deepseek-flex'
    raise ValueError('Unknown accounting role in split-route run')


def memory_profile():
    return 'luna-bedrock-global' if split_enabled() else PROFILE_NAME


def memory_model():
    return PROFILES[memory_profile()]['model']


def is_memory_model(model):
    return model == memory_model()


def record():
    if not split_enabled():
        return None
    return {'condition': CONDITION, 'memory_provider': dict(PROFILES['luna-bedrock-global']),
            'hermes': dict(PROFILES['deepseek-flex']), 'judge': dict(PROFILES['deepseek-flex']),
            'selection': 'Persistent memory/driver/judge accounting capability, including native recall and background calls; never inferred from wall-clock phase.'}


def liability_envelope(calls, limit, cohort_id, *, cap='20'):
    """A resumed child needs funding for the remaining envelope, not twice its spent part."""
    from decimal import Decimal
    total=sum((Decimal(str(r['accounted'])) for r in calls),Decimal(0))
    own=sum((Decimal(str(r['accounted'])) for r in calls if r.get('cohort_id')==cohort_id),Decimal(0))
    available=Decimal(cap)-total
    remaining=max(Decimal(0),Decimal(limit)-own)
    return {'available_usd':str(available),'cohort_accounted_usd':str(own),
            'remaining_envelope_usd':str(remaining),'additional_headroom_usd':str(max(Decimal(0),remaining-available)),
            'funded':remaining<=available}
