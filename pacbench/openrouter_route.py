"""Offline validation of the explicitly selected OpenRouter route.

This module does not hold credentials, make requests, or enforce a spending cap.
A caller must independently enforce authorization and a hard spending limit.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

MODEL = 'deepseek/deepseek-v4.1-flash-1017'
ENDPOINT = 'deepinfra/fp8/flex'
CHAT_COMPLETIONS_URL = 'https://openrouter.ai/api/v1/chat/completions'
CATALOG_URL = f'https://openrouter.ai/api/v1/models/{MODEL}/endpoints'
PROVIDER = {'only': [ENDPOINT], 'order': [ENDPOINT],
            'allow_fallbacks': False, 'require_parameters': True}
# These request fields can alter routing or substitute a model independently
# of the provider object. Fail closed rather than silently discarding them.
FORBIDDEN_FIELDS = frozenset({'models', 'route', 'plugins', 'transforms'})


def validate_request(request: Mapping[str, Any]) -> None:
    """Reject requests that do not declare precisely the selected route."""
    if not isinstance(request, Mapping):
        raise ValueError('request must be a mapping')
    if request.get('model') != MODEL:
        raise ValueError('exact model required')
    if FORBIDDEN_FIELDS.intersection(request):
        raise ValueError('alternative routing is forbidden')
    provider = request.get('provider')
    if not isinstance(provider, dict) or provider != PROVIDER:
        raise ValueError('exact provider route required')
    # bool is an int subclass; equality alone accepts 0/1 for these fields.
    if provider['allow_fallbacks'] is not False or provider['require_parameters'] is not True:
        raise ValueError('provider flags must be booleans')


def with_route(request: Mapping[str, Any]) -> dict[str, Any]:
    """Return a routed copy, refusing conflicting caller-supplied settings."""
    if not isinstance(request, Mapping):
        raise ValueError('request must be a mapping')
    result = deepcopy(dict(request))
    result.setdefault('model', MODEL)
    result.setdefault('provider', deepcopy(PROVIDER))
    validate_request(result)
    return result
