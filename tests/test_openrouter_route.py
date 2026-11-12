import copy

import pytest

from pacbench.openrouter_route import ENDPOINT, MODEL, PROVIDER, validate_request, with_route


def test_route_is_exact_and_copied():
    original = {'messages': [{'role': 'user', 'content': 'test'}]}
    routed = with_route(original)
    assert routed['model'] == MODEL
    assert routed['provider'] == {'only': [ENDPOINT], 'order': [ENDPOINT],
                                  'allow_fallbacks': False, 'require_parameters': True}
    routed['provider']['only'].append('other')
    assert PROVIDER['only'] == [ENDPOINT]
    assert 'provider' not in original


@pytest.mark.parametrize('change', [
    {'model': 'deepseek/deepseek-v4.1-flash'},
    {'models': [MODEL, 'other']}, {'route': 'fallback'},
    {'plugins': []}, {'transforms': []},
    {'provider': {'only': [ENDPOINT], 'order': [ENDPOINT],
                  'allow_fallbacks': True, 'require_parameters': True}},
    {'provider': {'only': [ENDPOINT], 'order': [ENDPOINT],
                  'allow_fallbacks': False, 'require_parameters': False}},
    {'provider': {'only': [ENDPOINT], 'order': [ENDPOINT],
                  'allow_fallbacks': 0, 'require_parameters': 1}},
    {'provider': {'order': [ENDPOINT], 'allow_fallbacks': False, 'require_parameters': True}},
    {'provider': {'only': ['deepinfra/fp8'], 'order': [ENDPOINT],
                  'allow_fallbacks': False, 'require_parameters': True}},
    {'provider': {**PROVIDER, 'sort': 'price'}},
])
def test_rejects_route_changes(change):
    with pytest.raises(ValueError):
        with_route(change)


def test_validation_requires_route():
    with pytest.raises(ValueError):
        validate_request({'model': MODEL})
    valid = {'model': MODEL, 'provider': copy.deepcopy(PROVIDER)}
    validate_request(valid)
