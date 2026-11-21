"""Read native Hindsight call boundaries without modifying the SDK or its inputs."""
import sys
from contextvars import Token
from threading import Lock
from uuid import UUID


_calls = {}
_lock = Lock()
_CONSOLIDATION = {'consolidation', 'consolidation_dedup'}


def native_consolidation_call(bank):
    # The run-owned server imports this module during ordinary initialization.
    # Avoid importing the server or building a trace from the controller.
    native = sys.modules.get('hindsight_api.engine.llm_trace')
    if native is None:
        return None
    operation = native.current_trace_context()
    if operation is None or operation.operation not in _CONSOLIDATION:
        return None
    if operation.bank_id != bank:
        raise ValueError('Hindsight consolidation call has no scoped native request')
    request_token = native_request_token(native)
    trace = str(UUID(operation.trace_id))
    span = str(UUID(operation.operation_span_id))
    owner = (trace, span, operation.operation)
    with _lock:
        # The SDK binds this token once per logical call, including calls
        # with no optional request parameters, until all its retries exit.
        previous = _calls.get(id(request_token))
        if previous is None:
            previous = (request_token, owner, len(_calls)+1)
            _calls[id(request_token)] = previous
        if previous[0] is not request_token or previous[1] != owner:
            raise ValueError('Hindsight native request context changed its identity')
    return {'kind':'hindsight-consolidation-call-v1','native_trace':trace,
            'native_operation_span':span,'native_operation':operation.operation,'ordinal':previous[2]}


def native_request_token(native):
    wrapper = sys.modules['hindsight_api.engine.llm_wrapper'].LLMProvider
    native_codes = {wrapper._call.__code__, wrapper._call_with_tools.__code__}
    frame = sys._getframe(1)
    while frame is not None:
        if frame.f_code in native_codes:
            value = frame.f_locals.get('request_token')
            if isinstance(value, Token) and value.var is native._request_ctx:
                return value
            break
        frame = frame.f_back
    raise ValueError('Hindsight transport does not belong to a native consolidation call boundary')
