"""Exercise the actual patched dispatch guards without launching paid model calls."""
import ast
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pacbench.harness import TurnResult, enforce_provider_tools

TREE = Path(__file__).resolve().parents[1] / 'vendor/hermes-isolated'


def native_source_guard():
    tree=ast.parse((TREE/'hermes_time.py').read_text())
    fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='source_read_blocked')
    namespace={}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[])),
                 'actual_native_source_guard','exec'),namespace)
    return namespace['source_read_blocked']


@pytest.fixture(autouse=True)
def native_policy_import(monkeypatch):
    monkeypatch.setitem(sys.modules,'hermes_time',SimpleNamespace(source_read_blocked=native_source_guard()))


def guard(relative, name, stop):
    tree = ast.parse((TREE / relative).read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    # Keep the real guard; replace the remaining dispatch with an observable sentinel.
    index = next(i for i, n in enumerate(fn.body) if stop(n))
    fn.body = fn.body[:index] + ast.parse("return 'EXECUTED'").body
    module = ast.Module(body=[fn], type_ignores=[])
    namespace = {'os': os, 'json': json, 'Optional': lambda x: x,
                 'InlineToolExecutor': object, '_ToolCallRef': object,
                 '_SequentialDispatch': lambda callback: callback}
    # Ignore annotations; this test needs only dispatch behavior.
    fn.returns = None
    for arg in fn.args.args:
        arg.annotation = None
    exec(compile(ast.fix_missing_locations(module), str(TREE / relative), 'exec'), namespace)
    return namespace[name]


@pytest.mark.parametrize('name', ['terminal', 'read_file', 'code_execution', 'tool_call',
                                 'tool_search', 'memory', 'session_search', 'hindsight_fake'])
def test_actual_inline_guard_blocks_non_provider_and_spoofed_names(monkeypatch, name):
    monkeypatch.setenv('PACBENCH_PROVIDER_ONLY', '1')
    monkeypatch.setenv('PACBENCH_TOOL_ALLOWLIST', 'hindsight_recall,hindsight_reflect')
    fn = guard('agent/inline_tool_executors.py', 'resolve_invoke_tool_executor',
               lambda n: isinstance(n, ast.If) and 'INVOKE_TOOL_PRE_MEMORY_MANAGER_NAMES' in ast.unparse(n.test))
    result = fn(None, name)(None, {}, None)
    assert 'PACBENCH-BLOCKED' in json.loads(result)['error']
    assert fn(None, 'hindsight_recall') == 'EXECUTED'


@pytest.mark.parametrize('allowlist', ['', 'hindsight*'])
def test_strict_mode_fails_closed_without_exact_tool_names(monkeypatch, allowlist):
    monkeypatch.setenv('PACBENCH_PROVIDER_ONLY', '1')
    monkeypatch.setenv('PACBENCH_TOOL_ALLOWLIST', allowlist)
    fn = guard('agent/inline_tool_executors.py', 'resolve_invoke_tool_executor',
               lambda n: isinstance(n, ast.If) and 'INVOKE_TOOL_PRE_MEMORY_MANAGER_NAMES' in ast.unparse(n.test))
    assert 'PACBENCH-BLOCKED' in fn(None, 'hindsight_recall')(None, {}, None)


def test_sequential_guard_blocks_before_executor_resolution(monkeypatch):
    monkeypatch.setenv('PACBENCH_PROVIDER_ONLY', '1')
    monkeypatch.setenv('PACBENCH_TOOL_ALLOWLIST', 'hindsight_recall')
    fn = guard('agent/tool_executor.py', '_resolve_sequential_dispatch',
               lambda n: isinstance(n, ast.If) and 'INLINE_TOOL_EXECUTORS' in ast.unparse(n.test))
    ref = SimpleNamespace(name='terminal', args={}, task_id=None, call_id=None, trace=[])
    assert 'PACBENCH-BLOCKED' in fn(None, ref, [])({})
    ref.name = 'hindsight_recall'
    assert fn(None, ref, []) == 'EXECUTED'


def test_attempt_fails_even_if_agent_answers_correctly():
    turn = TurnResult(prompt='', at='', answer='Correct answer',
                      tool_calls=[{'name': 'read_file'}], tool_results=[],
                      policy_blocks=0, latency_s=0, returncode=0, stderr_tail='', raw_events=1)
    enforce_provider_tools(turn, {'PACBENCH_PROVIDER_ONLY': '1',
                                 'PACBENCH_TOOL_ALLOWLIST': 'hindsight_recall'})
    assert turn.policy_blocks == 1


def test_actual_schema_filter_keeps_only_native_provider_tools(monkeypatch, tmp_path):
    monkeypatch.setenv('PACBENCH_PROVIDER_ONLY', '1')
    monkeypatch.setenv('PACBENCH_TOOL_ALLOWLIST', 'hindsight_recall,hindsight_reflect')
    monkeypatch.setenv('PACBENCH_CONTEXT_TRACE', str(tmp_path / 'context.jsonl'))
    tree = ast.parse((TREE / 'agent/turn_context.py').read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'build_turn_context')
    policy = next(n for n in fn.body if isinstance(n, ast.If) and 'PACBENCH_PROVIDER_ONLY' in ast.unparse(n.test))
    agent = SimpleNamespace(tools=[{'function': {'name': n}} for n in
                            ('terminal', 'memory', 'tool_search', 'hindsight_recall', 'hindsight_fake')])
    exec(compile(ast.fix_missing_locations(ast.Module(body=[policy], type_ignores=[])),
                 'actual_schema_filter', 'exec'), {'agent': agent, 'os': os, 'json': json})
    assert agent.valid_tool_names == {'hindsight_recall'}
    evidence = json.loads((tmp_path / 'context.jsonl.tools.jsonl').read_text())
    assert evidence['offered_tools'] == ['hindsight_recall']


def test_registry_guard_blocks_before_registry_or_hooks(monkeypatch):
    monkeypatch.setenv('PACBENCH_PROVIDER_ONLY', '1')
    monkeypatch.setenv('PACBENCH_TOOL_ALLOWLIST', 'hindsight_recall')
    tree = ast.parse((TREE / 'model_tools.py').read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'handle_function_call')
    start = next(i for i, n in enumerate(fn.body) if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == '_pacbench_allowlist' for t in n.targets))
    fn.body = fn.body[start:start + 2] + ast.parse("return 'EXECUTED'").body
    fn.returns = None
    for arg in fn.args.args: arg.annotation = None
    namespace = {'os': os, 'json': json}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[])),
                 'actual_registry_guard', 'exec'), namespace)
    invoke = namespace['handle_function_call']
    assert 'PACBENCH-BLOCKED' in invoke('terminal', {})
    assert invoke('hindsight_recall', {}) == 'EXECUTED'


def test_retained_memory_policy_excludes_every_memcal_source_reader():
    from pacbench.tool_policy import policy_record
    from pacbench.followups import tool_category, SOURCE_CATEGORIES
    tools=['memcal_open','memcal_search_wiki','memcal_activity','memcal_conversation',
           'memcal_search_archive','memcal_open_source']
    policy=policy_record('memcal',tools)
    assert policy['allowed_tools']==['memcal_open','memcal_search_wiki']
    assert len(policy['blocked_source_tools'])==4
    assert all(tool_category(n,{}) in SOURCE_CATEGORIES for n in policy['blocked_source_tools'])


@pytest.mark.parametrize('name',['memcal_activity','memcal_conversation','memcal_search_archive','memcal_open_source'])
def test_actual_dispatch_blocks_source_rescue_before_execution(monkeypatch,name):
    from pacbench.tool_policy import retained_tools
    allowed,_=retained_tools(['memcal_open',name])
    monkeypatch.setenv('PACBENCH_PROVIDER_ONLY','1')
    monkeypatch.setenv('PACBENCH_TOOL_ALLOWLIST',','.join(allowed))
    fn=guard('agent/inline_tool_executors.py','resolve_invoke_tool_executor',
        lambda n:isinstance(n,ast.If) and 'INVOKE_TOOL_PRE_MEMORY_MANAGER_NAMES' in ast.unparse(n.test))
    assert 'PACBENCH-BLOCKED' in fn(None,name)(None,{},None)
    assert fn(None,'memcal_open')=='EXECUTED'


def test_mixed_reader_is_offered_with_argument_constraints():
    from pacbench.tool_policy import retained_tools
    assert retained_tools(['viking_read','viking_search'])==(['viking_read','viking_search'],[])


@pytest.mark.parametrize('names',[None,[],[None],['memcal_open',1],['memcal*'],'memcal_open'])
def test_malformed_native_schemas_fail_closed(names):
    from pacbench.tool_policy import retained_tools
    with pytest.raises(ValueError):
        retained_tools(names)


READ_CASES=[
 ({'uri':'viking://resources/day/messages.txt','level':'full'},True),
 ({'uri':'viking://resources/day/messages.txt','level':'overview'},True),
 ({'uri':'viking://resources/day'},True),
 ({'uri':'viking://resources/day/.overview.md'},False),
 ({'uri':'viking://resources/day/.abstract.md','level':'abstract'},False),
 ({'uri':'viking://resources/day/.overview.md','level':'full'},True),
 ({'uri':'viking://user/default/memories/plans.md','level':'full'},False),
 ({'uri':'viking://~/memories/plans.md'},False),
 ({'uri':'viking://resources/day/memories/raw.txt','level':'full'},True),
 ({'uri':'viking://user/default/memories/../../sessions/raw.txt','level':'full'},True),
 ({'uri':'viking://user/default/memories/%2e%2e/%2e%2e/sessions/raw.txt','level':'full'},True),
 ({'uris':['viking://resources/day/.overview.md','viking://resources/day/messages.txt']},True),
 ({'uri':['viking://user/default/memories/plans.md','viking://resources/day/messages.txt']},True),
 ({'uri':'viking://resources/day/messages.txt?fake=/memories/','level':'full'},True),
 ({'uri':'viking://user/default/sessions/messages.jsonl','level':'full'},True),
 ({},True),
]


@pytest.mark.parametrize('arguments,blocked',READ_CASES)
def test_actual_mixed_reader_guards_before_native_execution(monkeypatch,arguments,blocked):
    from pacbench.source_policy import SOURCE_ARGUMENT_RULES, source_read_blocked
    monkeypatch.setenv('PACBENCH_PROVIDER_ONLY','1')
    monkeypatch.setenv('PACBENCH_TOOL_ALLOWLIST','viking_read')
    monkeypatch.setenv('PACBENCH_SOURCE_ARGUMENT_RULES',json.dumps(SOURCE_ARGUMENT_RULES))
    executed=[]
    manager=SimpleNamespace(handle_tool_call=lambda name,args:executed.append((name,args)) or 'EXECUTED')
    agent=SimpleNamespace(_memory_manager=manager)
    inline=guard('agent/inline_tool_executors.py','resolve_invoke_tool_executor',
        lambda n:isinstance(n,ast.If) and 'INVOKE_TOOL_PRE_MEMORY_MANAGER_NAMES' in ast.unparse(n.test))
    result=inline(agent,'viking_read')(agent,arguments,None)
    assert ('PACBENCH-BLOCKED' in result)==blocked
    assert bool(executed)==(not blocked)
    executed.clear()
    sequential=guard('agent/tool_executor.py','_resolve_sequential_dispatch',
        lambda n:isinstance(n,ast.If) and 'INLINE_TOOL_EXECUTORS' in ast.unparse(n.test))
    ref=SimpleNamespace(name='viking_read',args=arguments,task_id=None,call_id=None,trace=[])
    result=sequential(agent,ref,[])(arguments)
    assert ('PACBENCH-BLOCKED' in result)==blocked
    assert bool(executed)==(not blocked)
    assert native_source_guard()('viking_read',arguments,SOURCE_ARGUMENT_RULES)==source_read_blocked('viking_read',arguments,SOURCE_ARGUMENT_RULES)==blocked
    turn=TurnResult(prompt='',at='',answer='Correct answer',tool_calls=[{'name':'viking_read','arguments':arguments}],tool_results=[],policy_blocks=0,latency_s=0,returncode=0,stderr_tail='',raw_events=0)
    enforce_provider_tools(turn,{'PACBENCH_PROVIDER_ONLY':'1','PACBENCH_TOOL_ALLOWLIST':'viking_read'})
    assert turn.policy_blocks==int(blocked)


def test_honcho_original_messages_are_excluded_but_native_reasoning_remains():
    from pacbench.tool_policy import retained_tools
    from pacbench.followups import tool_category
    assert retained_tools(['honcho_search','honcho_context','honcho_profile',
                           'honcho_reasoning','honcho_conclude']) == (
        ['honcho_conclude','honcho_profile','honcho_reasoning'],
        ['honcho_context','honcho_search'])
    assert tool_category('honcho_reasoning',{}) == 'memory_reasoning'
    assert tool_category('honcho_conclude',{'list':True}) == 'memory_search'
    assert tool_category('honcho_conclude',{'conclusion':'A fact'}) == 'memory_write'
