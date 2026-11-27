"""Run a frozen corpus against native providers and persist each ingestion, turn and grade."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from pacbench import harness
from pacbench.context_isolation import judge_question
from pacbench.evidence_store import save
from pacbench.gateway_client import (
    ModelAccessBlocked,
    SpendingBlocked,
    client_settings,
    load_driver,
    require_live_cost_boundary,
)
from pacbench.judging.grade import (
    LEGACY_DIAGNOSTIC_VERSION,
    LEGACY_JUDGE_PROTOCOL,
    BenchmarkContractError,
    Claim,
    grade_native_semantic,
)
from pacbench.judging.recovery import freeze_policy, initial_effort, run_followups
from pacbench.model_calls import ModelClient
from pacbench.process_cleanup import OperatorStop, check_stop
from pacbench.providers import ACTIVE_PROVIDERS, require_not_retired
from pacbench.providers import provision as provider_setup
from pacbench.source_policy import VERSION as TOOL_POLICY
from pacbench.source_policy import policy_record
from pacbench.usage import tracked_turn
from pacbench.workflows.turn_evidence import (
    advance,
    diagnostic_inputs,
    fresh_state,
    session_key,
)


def next_checkpoint(rows, at):
    """Order instants, including the repeated hour when Eastern DST ends."""
    later = [r['at'] for r in rows if datetime.fromisoformat(r['at']) > datetime.fromisoformat(at)]
    return min(later, key=datetime.fromisoformat) if later else None


def copy_prepared_files(source, output):
    """Copy frozen run inputs together with their exact shared evidence store."""
    for name in ('bundle.json','contract.json','resolved-spec.json','shared-text.json'):
        if name=='shared-text.json' and not (source/name).exists():continue
        shutil.copyfile(source/name,output/name)
    from pacbench.corpus.contract import copy_review_evidence
    copy_review_evidence(source,output)


def check_provider_health(root, env):
    from pacbench.usage import provider_model_health, snapshot

    require_live_cost_boundary()
    health = provider_model_health(snapshot(json.loads(env['PACBENCH_USAGE_ROLES'])))
    save(root / 'provider-model-health.json', health)
    if health['state'] == 'stop':
        raise RuntimeError(health['reason'])


def ingest_pending(needed, ingested, failed_batches, output, root, provider, env, timezone, stop_file):
    """Save each native receipt before checking readiness, health or an operator stop."""
    from pacbench.workflows.delivery import ingest_day

    completed = set(ingested)
    for batch in needed:
        identifier = batch['id']
        if identifier in completed:
            continue
        check_stop(stop_file)
        if identifier in failed_batches:
            raise RuntimeError('Earlier native import failed; source state unavailable')
        # An exception may follow a real external write. Until the receipt is
        # durable and ready, later questions must treat this batch as failed.
        failed_batches.add(identifier)
        record = ingest_day(provider, output / 'sources' / identifier, root, env, batch['at'], timezone)
        save(root / batch['file'], record)
        ready = record.get('state') == 'ready' and record.get('ready') is True
        if ready:
            ingested.append(identifier)
            completed.add(identifier)
            failed_batches.remove(identifier)
        error = (record.get('result') or {}).get('native_error') or record.get('native_error') or {}
        if error.get('authentication_blocked') is True or error.get('http_status') in (401, 403):
            raise ModelAccessBlocked('Native provider authentication blocked; live run stopped')
        check_stop(stop_file)
        check_provider_health(root, env)
        if not ready:
            raise RuntimeError('Native source ingestion/processing failed; inspect saved record')


def read_prepared(source: Path, max_messages: int | None) -> tuple[list[dict], object, dict]:
    """Compatibility tuple for callers reading an existing prepared directory."""
    from pacbench.corpus.audit import open_prepared

    with open_prepared(source, max_messages=max_messages) as corpus:
        return corpus.messages, corpus.contract, corpus.resolved


def run(source: Path | None, output: Path, max_messages: int | None = 150,
        repetitions: int = 1, *, smoke: bool = False, providers: list[str] | None = None,
        question_dir: Path | None = None, honcho_clock: str = 'real',
        inference_route: str = 'muse-gateway', judge_reasoning_effort: str | None = None,
        judge_timeout_s: int = 180, recall_timeout_s: int = 180, report_blocked: bool = False) -> dict:
    for provider in providers or []:require_not_retired(provider)
    from pacbench.providers import require_live_trials_ready
    require_live_trials_ready()
    from pacbench.workflows.delivery import (
        BLOCKERS,
        prepare_sources,
        record_recall_isolation,
    )
    from pacbench.workflows.source_plan import validate_evaluation_clocks
    if inference_route not in ('muse-gateway', 'codex-subscription', 'openrouter'):
        raise ValueError('Unknown inference route')
    if type(judge_timeout_s) is not int or not 0<judge_timeout_s<=900:
        raise ValueError('Judge deadline must be 1–900 real seconds')
    if type(recall_timeout_s) is not int or not 0<recall_timeout_s<=900:
        raise ValueError('Recall deadline must be 1–900 real seconds')
    if judge_reasoning_effort is not None and (inference_route!='openrouter' or
            judge_reasoning_effort not in {'none','low','medium','high'}):
        raise ValueError('Explicit judge reasoning requires the direct OpenRouter condition')
    if not 1 <= repetitions <= 3 or max_messages is not None and not 1 <= max_messages <= 100000:
        raise ValueError("pilot limits: 1–3 repetitions, 1–100000 messages; increase gradually")
    selected = providers if providers is not None else provider_setup.provider_names()
    unique = set(selected)
    if (not selected or len(unique) != len(selected)
            or providers is not None and not unique.issubset(provider_setup.provider_names())):
        raise ValueError("invalid or duplicate provider selection")
    if honcho_clock not in ('real', 'simulated') or (honcho_clock == 'simulated' and (not source or 'honcho' not in selected)):
        raise ValueError('A simulated Honcho clock requires prepared source and the Honcho provider')
    if inference_route == 'openrouter':
        if any(p not in ACTIVE_PROVIDERS for p in selected):
            raise ValueError('Unsupported OpenRouter memory provider')
        if os.environ.get('PACBENCH_TRANSPORT') != 'openrouter':
            raise RuntimeError('OpenRouter requires the run-local transport; key and spending limit must be configured first')
        if not (source and report_blocked):
            require_live_cost_boundary()
    elif os.environ.get('PACBENCH_TRANSPORT') == 'openrouter':
        raise ValueError('Transport and inference route differ')
    if source and inference_route == 'codex-subscription':
        raise RuntimeError('Codex native pilot is blocked: an isolated Hermes OAuth provision/login/resume workflow is not implemented; no ingestion or inference was started')
    if source:
        from pacbench.context_isolation import validate_recall_environment
        validate_recall_environment()
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    stop_file = Path(os.environ.get('PACBENCH_STOP_FILE') or output / 'STOP').resolve()
    question_dir=(question_dir or output/'combined-questions').resolve()
    # Registered first, so native server shutdown hooks run before this final
    # fallback for CLI grandchildren left behind by timed-out provider tools.
    import atexit

    from pacbench.process_cleanup import cleanup_run_processes
    atexit.register(cleanup_run_processes, output)
    results = []
    report = {"state": "inventory", "results": results, "mode": "native-hermes",
              "builtin_memory": False, "tool_policy": TOOL_POLICY, "session_search": False,
              'ingestion_mode':'documents', 'selected_providers': selected}
    report['inference_route'] = inference_route
    if judge_reasoning_effort is not None:
        report['judge_generation_conditions']={'reasoning_effort':judge_reasoning_effort,
            'scope':'Benchmark judge only; native provider and Hermes settings unchanged'}
    report.setdefault('judge_generation_conditions',{})['wall_deadline_s']=judge_timeout_s
    report['recall_wall_deadline_s']=recall_timeout_s
    if 'honcho' in selected and os.environ.get('DERIVER_MODEL_CONFIG__THINKING_EFFORT'):
        report['provider_generation_conditions'] = {'honcho': {
            'deriver_thinking_effort': os.environ['DERIVER_MODEL_CONFIG__THINKING_EFFORT'],
            'scope': 'Documented native model setting; output limits and batching remain unchanged'}}
    if inference_route == 'openrouter':
        from pacbench.openrouter_proxy import VERSION as TRANSPORT_VERSION
        from pacbench.openrouter_route import ENDPOINT, MODEL
        report.update(hermes_model=MODEL, judge_model=MODEL, extraction_model=MODEL,
                      openrouter_endpoint=ENDPOINT, transport=TRANSPORT_VERSION)
    if inference_route == 'codex-subscription':
        report['hermes_model'] = report['judge_model'] = 'gpt-6-luna'
        report['subscription_cost_status'] = 'unpriced'
    report['provenance_version'] = ('source-provenance-v3' if any(p in {'cognee','memos','supermemory'} for p in selected) else 'source-provenance-v2')
    report['provider_loader_protocol']='native-loaded-source-v1'
    if source and 'openviking' in selected:
        from pacbench.native_writes import TERMINAL_PROTOCOL as TERMINAL_PROTOCOL
        report['terminal_processing_protocols']={'openviking':TERMINAL_PROTOCOL}
    from pacbench.context_isolation import NATIVE_QUESTION as CLOCK_CONTEXT
    report['clock_context_protocol']=CLOCK_CONTEXT
    report['question_prompt_protocol']=harness.PROMPT_PROTOCOL
    if source and 'honcho' in selected:
        from provider_adapters.honcho_enrollment import PROTOCOL as HONCHO_MEMBERSHIP
        report['provider_membership_protocols'] = {'honcho': HONCHO_MEMBERSHIP}
    if honcho_clock == 'simulated':
        from provider_adapters.honcho_clock import VERSION as HONCHO_CLOCK
        report['provider_clocks'] = {'honcho': HONCHO_CLOCK}
    from pacbench.judging.grade import DATE_SCOPED_JUDGE_PROTOCOL, DIAGNOSTIC_VERSION
    report['evidence_diagnostic_protocol'] = DIAGNOSTIC_VERSION
    report['judge_protocol'] = DATE_SCOPED_JUDGE_PROTOCOL
    save(output / "summary.json", report)
    driver = None
    model_judge = plan = None
    messages = contract = resolved = None
    process_timezone = ''
    evaluations = []
    if source:
        from pacbench.corpus.audit import PreparedCorpusError, open_prepared

        try:
            with open_prepared(source, destination=output / 'preparation', max_messages=max_messages) as corpus:
                source = corpus.root
                messages, contract, resolved = corpus.messages, corpus.contract, corpus.resolved
                evaluation_plan, evaluations = corpus.evaluation_plan, corpus.evaluations
                if corpus.corpus_hash is not None:
                    report['corpus_hash'] = corpus.corpus_hash
                report.update(input_hash=corpus.input_hash, contract_hash=corpus.summary['contract_hash'],
                              bundle_hash=corpus.summary['bundle_hash'], message_count=len(messages),
                              source=os.path.relpath(source, output))
        except PreparedCorpusError as exc:
            report.update(state='incomplete', failure_phase='input', reason=str(exc))
            save(output / 'summary.json', report)
            raise
        try:
            report["gateway_before"] = require_live_cost_boundary()
            driver = load_driver()
        except RuntimeError as exc:
            report.update(state="blocked", reason=str(exc))
            save(output / "summary.json", report)
            if report_blocked:
                return report
            raise
        copy_prepared_files(source,output)
        process_timezone = resolved.get('timezone', '')
        report['process_timezone'] = process_timezone
        source_timezone = process_timezone or 'America/New_York'
        logical_documents = os.environ.get('PACBENCH_LOGICAL_DOCUMENTS_ONLY') == '1'
        from pacbench.workflows.source_plan import VERSION as INGESTION_VERSION
        report.update(ingestion_version=INGESTION_VERSION,
                      document_provider_blockers=dict(BLOCKERS),
                      ingestion_schedule=prepare_sources(output, messages, source_timezone,
                          native_fixtures=not logical_documents or 'memcal' in selected))
        report['source_transport_condition']='logical-document-metadata-v1' if logical_documents else 'native-source-fixtures-v1'
        report['strict_memory_only']=os.environ.get('PACBENCH_STRICT_MEMORY_ONLY')=='1'
        source_timelines = validate_evaluation_clocks(report['ingestion_schedule'], [p for p in selected if p not in BLOCKERS],
            [row['at'] for row,_,_ in evaluations] if evaluations else [resolved['question_at']],
            source_timezone, chronological=bool(evaluations))
        if evaluation_plan:
            from pacbench.native_writes import VERSION as WRITE_READINESS
            report['write_readiness_protocol']=WRITE_READINESS
            shutil.copyfile(source / 'evaluation-plan.json', output / 'evaluation-plan.json')
            shutil.copytree(source / 'evaluations', output / 'evaluations')
            report.update(evaluation_plan_hash=evaluation_plan['plan_hash'], evaluation_context=evaluation_plan['context'])
        judge_base, judge_token = client_settings("pacbench-judge")
        model_judge = ModelClient(key=judge_token, model=driver.model_id,
                            url=judge_base + "/chat/completions",
                            record_dir=output / "model-calls", reasoning_effort=judge_reasoning_effort,
                            json_mode=inference_route=='openrouter',timeout_s=judge_timeout_s)
        plan = freeze_policy(contract, output / "followup-plan.json")
        report["followup_plan_hash"] = plan["plan_hash"]
        report["followup_policy"] = "one combined message for implicit omissions only; wrong answers, explicit misses and admitted retrieval failures are terminal; half recovery credit; source fallback is permitted but earns no retention credit"
        report["state"] = "running"
        save(output / "summary.json", report)
    interrupted = False
    live_stopped = False
    def evaluate_arm(root, provider, env, result):
        nonlocal interrupted, live_stopped
        capability = result['capability']
        simulated = provider == 'honcho' and honcho_clock == 'simulated'

        def judge(*args, **kwargs):
            check_stop(stop_file)
            if simulated:
                from provider_adapters.honcho_clock import benchmark_overhead
                with benchmark_overhead(root, json.loads(env['PACBENCH_USAGE_ROLES'])):
                    return model_judge(*args, **kwargs)
            return model_judge(*args, **kwargs)

        def guarded_turn(*args, **kwargs):
            check_stop(stop_file)
            return harness.run_turn(*args, **kwargs)

        try:
            policy = policy_record(provider, capability["tools"])
        except ValueError as exc:
            result.update(state="invalid", reason=str(exc))
            return False
        allowlist = ",".join(policy["allowed_tools"])
        env['PACBENCH_SOURCE_ARGUMENT_RULES'] = json.dumps(policy['source_argument_rules'])
        save(root / "tool-policy.json", policy)
        ingested=[]
        failed_batches=set()
        def import_before_check(row, directory):
            check_stop(stop_file)
            check_provider_health(root, env)
            needed = source_timelines[provider].ready(row['at'])
            if simulated:
                env['PACBENCH_CLOCK_HORIZON'] = row['at']
                later = next_checkpoint((r for r,_,_ in evaluations), row['at'])
                if later: env['PACBENCH_CLOCK_NEXT_CHECKPOINT'] = later
                else: env.pop('PACBENCH_CLOCK_NEXT_CHECKPOINT', None)
            ingest_pending(needed, ingested, failed_batches, output, root, provider, env,
                           source_timezone, stop_file)
            if not (root/'pre-recall-isolation.json').exists():record_recall_isolation(root)
            if simulated:
                from provider_adapters.honcho_clock import stage_turn
                # Age any genuine capture work before checking a later
                # session's dependency; never flush or backdate that work.
                stage_turn(root, row['at'], env)
            if evaluations:
                from pacbench.native_writes import ensure_ready
                ensure_ready(provider,row,evaluations,evaluation_plan['context'],root,env,directory)
            from pacbench.workflows.source_plan import source_state
            save(directory/'source-state.json',source_state(row['at'],ingested))
        def run_checkpoint_sequence():
            nonlocal interrupted, live_stopped
            from pacbench.corpus.evaluation import judge_evidence
            def evidence_for_check(row):
                return judge_evidence(output, messages, resolved, row, evaluation_plan['context'], diagnostic_version=report['evidence_diagnostic_protocol'])
            sequence = run_checks(evaluations, evaluation_plan['context'], root, judge, allowlist, env,
                                  question_dir=question_dir, before_check=import_before_check,
                                  evidence_by_id=evidence_for_check,
                                  turn_timeout_s=recall_timeout_s,
                                  provenance_version=report['provenance_version'], diagnostic_version=report['evidence_diagnostic_protocol'], judge_protocol=report['judge_protocol'])
            result.update(state=sequence['state'], checks=sequence['checks'], input_hash=report['input_hash'],
                          reason='' if sequence['state'] == 'diagnostic' else 'evaluation sequence incomplete')
            capability['live_verified'] = sequence['state'] == 'diagnostic'
            result['behavior'] = {'evaluation_context': evaluation_plan['context']}
            if sequence['state'] == 'interrupted':
                result['reason'] = 'operator stop requested'
                interrupted = True
            if sequence['state'] in ('access_blocked','budget_blocked'):
                live_stopped=True
                report['access_stop' if sequence['state']=='access_blocked' else 'spending_stop']=sequence['reason']

        def run_single_recall():
            """Return whether post-recall observations should run."""
            try:
                import_before_check({'at':resolved['question_at']},root)
            except (ModelAccessBlocked, SpendingBlocked):
                raise
            except RuntimeError as exc:
                result.update(state='invalid',reason=str(exc))
                return False
            # Deliberately omit --resume: recall must cross a real session boundary.
            check_stop(stop_file)
            turn = tracked_turn(harness.run_turn, contract.question, usage_path=root / "usage.json", at=resolved["question_at"],
                                    home=root / "hermes-home", allowlist=allowlist,
                                    extra_env=env, timeout_s=recall_timeout_s)
            save(root / "recall.json", vars(turn))
            check_stop(stop_file)
            result["behavior"] = {
                "provider_tool_calls": [c.get("name") for c in turn.tool_calls
                                        if c.get("name") in capability["tools"]],
                "prefetch_context_present": bool(turn.prefetch_context)}
            result.update(state="invalid", reason="recall turn failed or policy blocked")
            if not turn.returncode and turn.answer.strip() and not turn.policy_blocks:
                from pacbench.corpus.evaluation import (
                    source_evidence as build_source_evidence,
                )
                from pacbench.workflows.turn_evidence import diagnostic_inputs
                source_evidence = build_source_evidence(messages, report["evidence_diagnostic_protocol"])
                from pacbench.context_isolation import judge_question
                grade = grade_native_semantic(resolved["id"], judge_question(turn), turn.answer,
                                       tuple(Claim(c.id, c.statement, c.evidence)
                                             for c in contract.claims), judge,
                                       record_path=root / "judgment.json", assess_recovery=True,
                                       diagnostic_inputs=diagnostic_inputs(turn, contract, source_evidence, diagnostic_version=report['evidence_diagnostic_protocol']), diagnostic_version=report['evidence_diagnostic_protocol'], judge_protocol=report['judge_protocol'])
                save(root / "grade.json", {"correct": grade.correct, "missed": grade.missed,
                                          "misrepresented": grade.misrepresented,
                                          "claims": [vars(s) for s in grade.slots], "recovery": grade.recovery,
                                          "evidence_diagnostic": grade.evidence_diagnostic})
                result.update(state="diagnostic", reason="", input_hash=report['input_hash'])
                capability["live_verified"] = True
                recovery = run_followups(contract=contract, plan=plan, grade=grade,
                    initial_turn=turn, output=root / "followups", judge=judge,
                    home=root / "hermes-home", allowlist=allowlist, extra_env=env,
                    question_dir=question_dir, evidence_by_id=source_evidence, run_turn=guarded_turn,
                    provenance_version=report['provenance_version'], diagnostic_version=report['evidence_diagnostic_protocol'], judge_protocol=report['judge_protocol'])
                result["recovery"] = {"state": recovery["state"], "score": recovery["score"],
                    "attempts": [{"claim_ids": a["claim_ids"], "verdicts": a["verdicts"],
                                  "metrics": a["metrics"]} for a in recovery["attempts"]]}
                if recovery["state"] != "complete":
                    result.update(state="invalid", reason="follow-up diagnostics incomplete")
            return True

        if evaluations:
            run_checkpoint_sequence()
        elif not run_single_recall():
            return False

        if not (interrupted or live_stopped) and provider == 'openviking' and report.get('terminal_processing_protocols'):
            from pacbench.native_writes import observe_before_stop
            def terminal_boundary():
                check_stop(stop_file)
                check_provider_health(root, env)
            terminal=observe_before_stop(root,env,boundary=terminal_boundary)
            result['terminal_processing']={'protocol':terminal['protocol'],'state':terminal['state'],
                'scope_hash':terminal['scope_hash']}
            if terminal['state']!='complete':
                capability['recall_verified']=capability.get('live_verified',False)
                capability['live_verified']=False
                if result['state']=='diagnostic':
                    result.update(state='partial',failure_phase='terminal_processing',
                        reason='Post-answer native processing '+terminal['state']+'; semantic grades preserved')
            if terminal['state']=='stopped_by_boundary':
                if terminal.get('error_type')=='OperatorStop':interrupted=True
                else:live_stopped=True
        from pacbench.usage import snapshot
        save(root/'pre-shutdown-model-usage.json',snapshot(json.loads(env['PACBENCH_USAGE_ROLES'])))
        return True

    def run_arm(provider, repetition):
        nonlocal interrupted, live_stopped
        try:
            check_stop(stop_file)
        except OperatorStop:
            interrupted = True
            return

        root = output / f"repeat-{repetition}" / provider
        result = dict(provider=provider, repetition=repetition, state='running',
                      capability={'available': False, 'tools': []})
        if source:
            result['input_hash'] = report['input_hash']
        results.append(result)

        def block(reason):
            result.update(state='blocked', reason=reason, capability={
                'available': False, 'reason': reason, 'execution_blocker': reason,
                'wiring_verified': False, 'tools': [],
            })

        def local_failure(exc, phase):
            reason = f'Native {phase} failed: {type(exc).__name__}'
            try:
                _, key = client_settings('setup-check')
            except (OSError, RuntimeError, ValueError, KeyError):
                key = None
            if key:
                reason += ': ' + str(exc).replace(key, '<gateway-token>')
            result.update(state='invalid', reason=reason, failure_phase=phase,
                          capability={'available': False, 'reason': reason, 'tools': [], 'wiring_verified': False})

        needs_shutdown = collect_final_usage = False
        primary = None
        phase = 'setup'
        try:
            if source and provider in BLOCKERS:
                block(BLOCKERS[provider])
                return
            if source:
                try:
                    require_live_cost_boundary()
                except (ModelAccessBlocked, SpendingBlocked):
                    raise
                except RuntimeError as exc:
                    block(str(exc))
                    live_stopped = True
                    report['gateway_stop'] = str(exc)
                    return

            needs_shutdown = True
            try:
                clock_options = {}
                if provider == 'honcho' and honcho_clock == 'simulated':
                    from provider_adapters.honcho_enrollment import initial_at
                    clock_options['honcho_simulated_at'] = initial_at(messages)
                env = provider_setup.provision(root, provider, driver.model_id if driver else "unconfigured",
                                local_timezone=process_timezone,
                                inference_route=inference_route, **clock_options)
            except (ModelAccessBlocked, SpendingBlocked):
                raise
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
                local_failure(exc, 'setup')
                save(root/'simulation-clock.json', {
                    'process_timezone': process_timezone,
                    'pin': 'setup attempt; no evaluation',
                })
                return

            env['PACBENCH_STOP_FILE'] = str(stop_file)
            env['PACBENCH_CLOCK_CONTEXT_PROTOCOL'] = report['clock_context_protocol']
            check_stop(stop_file)
            save(root/'simulation-clock.json', {'process_timezone': env.get('TZ', ''),
                 'pin': 'public simulated clock supplied to every turn'})

            phase = 'probe'
            try:
                capability = provider_setup.probe(root, provider, env, smoke=smoke or source is not None, smoke_timeout_s=120)
            except (ModelAccessBlocked, SpendingBlocked):
                raise
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
                local_failure(exc, 'probe')
                return
            if (smoke or source is not None) and provider in provider_setup.ISOLATED_PROVIDERS:
                capability['wiring_verified'] = provider_setup.smoke_succeeded(capability)
            save(root/'preflight.json', capability)
            result.update(capability=capability, state=('available-unverified'
                if capability['available'] and not capability.get('execution_blocker') else 'blocked'))
            result.setdefault('input_hash', None)
            if report.get('write_readiness_protocol'):
                result['write_readiness_protocol'] = report['write_readiness_protocol']
            save(root/'status.json', result)
            save(output/'summary.json', report)

            if capability.get('wiring_verified') is False:
                result.update(state='invalid', reason='native read-only smoke failed')
            if (not source or not capability['available'] or capability.get('execution_blocker')
                    or result['state'] == 'invalid'):
                return
            phase = 'evaluation'
            from pacbench.openrouter_run import public_input_trace
            with public_input_trace(root, env):
                collect_final_usage = evaluate_arm(root, provider, env, result)
        except OperatorStop:
            result.update(state='interrupted', reason='operator stop requested')
            interrupted = True
        except (ModelAccessBlocked, SpendingBlocked) as exc:
            result.update(state='blocked', reason=str(exc))
            live_stopped = True
            report['access_stop' if isinstance(exc, ModelAccessBlocked) else 'spending_stop'] = str(exc)
        except BaseException as exc:
            primary = exc
            result.update(state='invalid', failure_phase=phase, reason=type(exc).__name__)
            report['state'] = 'incomplete'
            raise
        finally:
            try:
                if needs_shutdown:
                    provider_setup.stop_arm_model_work(root)
                if collect_final_usage:
                    from pacbench.usage import snapshot
                    save(root/'model-usage.json', snapshot(json.loads(env['PACBENCH_USAGE_ROLES'])))
            except BaseException as exc:
                report['state'] = 'incomplete'
                result.update(state='invalid', cleanup_error=type(exc).__name__)
                if primary is None:
                    raise
                primary.add_note(f'Native arm cleanup failed: {type(exc).__name__}')
            finally:
                save(root/'status.json', result)
                save(output/'summary.json', report)

    for repetition in range(1, repetitions + 1):
        for provider in selected:
            run_arm(provider, repetition)
            if interrupted or live_stopped:
                break
        if interrupted or live_stopped:
            break
    report["state"] = ("diagnostic" if all(r["state"] == "diagnostic" for r in results)
                       else "incomplete") if source else "inventory"
    if live_stopped:
        report['state'] = 'incomplete'
    if interrupted:
        report.update(state='interrupted', interruption='operator stop requested')
    if source:
        try:report["gateway_after"] = require_live_cost_boundary()
        except RuntimeError as exc:
            # Budget stopping is a completed execution outcome, not a reason
            # to leave the public run permanently marked "running".
            report['state']='interrupted' if interrupted else 'incomplete'
            key=('access_stop' if isinstance(exc,ModelAccessBlocked) else
                 'spending_stop' if isinstance(exc,SpendingBlocked) else 'gateway_stop')
            report[key]=str(exc)
    save(output / "summary.json", report)
    if source:
        from pacbench.native_audit import audit
        try:
            report["audit"] = audit(output)
        except Exception as exc:
            if not interrupted:
                report["state"] = "incomplete"
            report["audit"] = {'state': 'error', 'error_type': type(exc).__name__}
            save(output / "summary.json", report)
            raise
        if report["audit"]["state"] != "pass" and not interrupted:
            report["state"] = "incomplete"
        save(output / "native-audit.json", report["audit"])
        save(output / "summary.json", report)
    return report


def run_checks(checks, context, root, judge, allowlist, env, *, question_dir, resume_prefix=0,
               run_turn=None, budget_snapshot=None, before_check=None, evidence_by_id=None, provenance_version='source-provenance-v1', diagnostic_version=LEGACY_DIAGNOSTIC_VERSION, judge_protocol=LEGACY_JUDGE_PROTOCOL, turn_timeout_s=180, provider_name=None):
    from pacbench.process_cleanup import OperatorStop, check_stop
    native_turn = run_turn or harness.run_turn
    def run_turn(*args, **kwargs):
        check_stop(env.get('PACBENCH_STOP_FILE'))
        kwargs['timeout_s']=turn_timeout_s
        return native_turn(*args, **kwargs)
    sessions, outcomes = {}, {}
    report = {'state': 'running', 'context': context, 'checks': []}
    if provider_name:report['provider_name']=provider_name
    if env.get('PACBENCH_STRICT_MEMORY_ONLY',os.environ.get('PACBENCH_STRICT_MEMORY_ONLY'))=='1':report['strict_memory_protocol']='strict-memory-only-v1'
    root.mkdir(parents=True, exist_ok=True)
    if resume_prefix:
        previous=json.loads((root/'evaluation.json').read_text())
        if previous['context']!=context or len(previous['checks'])!=resume_prefix or previous.get('strict_memory_protocol')!=report.get('strict_memory_protocol') or previous.get('provider_name')!=report.get('provider_name'):raise ValueError('Resume prefix differs')
        for (row,contract,_),item in zip(checks[:resume_prefix],previous['checks']):
            if item['id']!=row['id'] or item['state'] not in {'diagnostic','strict-ungraded'} or item.get('recovery',{}).get('state','complete')!='complete':raise ValueError('Resume requires complete diagnostic prefix')
            directory=root/'turns'/row['id'];turn=harness.TurnResult(**json.loads((directory/'turn.json').read_text()))
            key=session_key(row,context);state=sessions.setdefault(key,fresh_state())
            if provider_name:state['provider_name']=provider_name
            report['checks'].append(item);outcomes[row['id']]=item
            if report.get('strict_memory_protocol'):
                from pacbench.source_policy import classify_retention as classify
                decision=classify(provider_name or root.name,turn,state.get('strict','eligible'))
                if decision!=json.loads((directory/'strict-memory.json').read_text()):raise ValueError('Strict recovery provenance differs')
                state['strict']=decision['state']
            advance(state,turn,directory,provenance_version)
    else:
        with (root / 'evaluation.json').open('x') as record:json.dump(report, record)
    for row, contract, policy in checks[resume_prefix:]:
        key = session_key(row, context)
        state = sessions.setdefault(key, fresh_state())
        if provider_name:state['provider_name']=provider_name
        directory = root / 'turns' / row['id']
        item = {'id': row['id'], 'at': row['at'], 'session': key, 'contract_hash': contract.contract_hash,
                'state': 'ungraded', 'verdict': None, 'reason': 'turn not complete'}
        report['checks'].append(item); outcomes[row['id']] = item
        save(root / 'evaluation.json', report)
        dependency = outcomes.get(row.get('depends_on'))
        missing=next((outcomes[r] for r in row.get('requires',[]) if outcomes[r]['state']!='diagnostic'),None)
        if state['failed'] or dependency and dependency['state'] != 'diagnostic' or missing:
            item.update(state='skipped', reason='Dependency unavailable', cause=state['failed'] or (missing or dependency)['id'])
        elif row.get('trigger') == 'clarification' and dependency.get('interaction') != 'clarification':
            item.update(state='skipped', reason='Scripted clarification reply was not requested', cause=dependency['id'])
        else:
            turn = None
            try:
                if before_check is not None:before_check(row, directory)
                turn = tracked_turn(run_turn, contract.question, usage_path=directory / 'usage.json', at=row['at'],
                    home=root / 'hermes-home', allowlist=allowlist, extra_env=env, timeout_s=turn_timeout_s,
                    session_id=state['session'], session_started_at=state['session_started_at'])
                save(directory / 'turn.json', vars(turn))
                if (turn.hermes_gateway or {}).get('version')=='checkpoint-session-v1':
                    from pacbench.workflows.turn_evidence import audit_transport
                    audit_transport(turn)
                check_stop(env.get('PACBENCH_STOP_FILE'))
                if turn.timed_out:
                    item.update(reason='Hermes turn exceeded real-time deadline',error_type='execution_timeout')
                    state['failed'] = row['id']
                elif turn.policy_blocks:
                    item.update(state='failed', verdict='fail', reason='Disallowed tool attempted')
                    state['failed'] = row['id']
                elif (turn.returncode or not turn.answer.strip() or not turn.session_id or
                        state['session'] and turn.session_id != state['session']):
                    item.update(reason='Hermes turn failed or lost conversation continuity', error_type='execution')
                    state['failed'] = row['id']
                else:
                    if report.get('strict_memory_protocol')=='strict-memory-only-v1':
                        from pacbench.source_policy import (
                            classify_retention as classify,
                        )
                        decision=classify(provider_name or root.name,turn,state.get('strict','eligible'))
                        state['strict']=decision['state']
                        save(directory/'strict-memory.json',decision)
                        if decision['state']!='eligible':
                            item.update(state='strict-ungraded',reason='Source-assisted or unverified memory provenance',verdict=None)
                            advance(state,turn,directory,provenance_version)
                            save(directory/'status.json',item);save(root/'evaluation.json',report)
                            continue
                    behavior = row.get('kind') == 'behavior'
                    interactive = 'session' in row or behavior
                    grade = grade_native_semantic(row['id'], judge_question(turn), turn.answer,
                        tuple(Claim(c.id, c.statement, c.evidence) for c in contract.claims), judge,
                        record_path=directory / 'judgment.json', conversation=state['history'],
                        assess_recovery=not behavior, assess_interaction=interactive,
                        diagnostic_inputs=diagnostic_inputs(turn, contract, evidence_by_id(row) if callable(evidence_by_id) else evidence_by_id, diagnostic_version=diagnostic_version) if evidence_by_id is not None else None, diagnostic_version=diagnostic_version, judge_protocol=judge_protocol)
                    save(directory / 'grade.json', {'correct': grade.correct, 'missed': grade.missed,
                        'misrepresented': grade.misrepresented, 'claims': [vars(c) for c in grade.slots],
                        'recovery': grade.recovery, 'interaction': grade.interaction,
                        **({'evidence_diagnostic': grade.evidence_diagnostic} if grade.evidence_diagnostic is not None else {})})
                    item.update(verdict='pass' if grade.all_claims_correct else 'fail', interaction=grade.interaction)
                    if behavior:
                        # Scripted story replies and acknowledgements never get
                        # an omission-recovery question or persistence credit.
                        save(directory / 'behavior.json', {'state':'complete',
                            'initial_metrics':initial_effort(turn,state['previous'],inherited_path=state['path'],provenance_version=provenance_version),
                            'observable_only':True})
                        item.update(state='diagnostic', reason='')
                    else:
                        recovery = run_followups(contract=contract, plan=policy, grade=grade, initial_turn=turn,
                            output=directory / 'followups', judge=judge, home=root / 'hermes-home',
                            allowlist=allowlist, extra_env=env, question_dir=question_dir,
                            previous_turn=state['previous'], conversation=state['history'], inherited_path=state['path'],
                            run_turn=run_turn, budget_snapshot=budget_snapshot, provenance_version=provenance_version,
                            evidence_by_id=(evidence_by_id(row) if callable(evidence_by_id) else evidence_by_id), diagnostic_version=diagnostic_version, judge_protocol=judge_protocol, provider_name=provider_name)
                        item.update(state='diagnostic',
                            reason='' if recovery['state']=='complete' else 'Recovery diagnostics incomplete',
                            recovery={'state':recovery['state'],'score':recovery['score'],'attempts':recovery['attempts'],
                                **({'score_state':recovery['score_state']} if 'score_state' in recovery else {})})
                    advance(state,turn,directory,provenance_version)
            except (ModelAccessBlocked,SpendingBlocked) as exc:
                item.update(state='ungraded',verdict=None,error_type='access_blocked' if isinstance(exc,ModelAccessBlocked) else 'budget_blocked',reason=str(exc))
                report.update(state=item['error_type'],reason=str(exc))
                save(directory/'status.json',item);save(root/'evaluation.json',report)
                return report
            except OperatorStop:
                item.update(state='interrupted', verdict=None, reason='operator stop requested')
                save(directory / 'status.json', item)
                report['state'] = 'interrupted'
                save(root / 'evaluation.json', report)
                return report
            except Exception as exc:
                # Keep completed public history even if its judge failed. An
                # execution failure poisons that session, not independent ones.
                if turn and not turn.returncode and not turn.policy_blocks and turn.session_id:
                    advance(state,turn,directory,provenance_version)
                else:state['failed'] = row['id']
                reason=f'{type(exc).__name__}: {exc}'
                token=env.get('OPENAI_API_KEY','')
                if token:reason=reason.replace(token,'<gateway-token>')
                item.update(state='ungraded',verdict=None,reason=reason,
                    error_type='benchmark_contract' if isinstance(exc,BenchmarkContractError) else 'execution_or_judge')
        save(directory / 'status.json', item)
        save(root / 'evaluation.json', report)
    report['state'] = ('diagnostic' if all((x['state']=='diagnostic' and
        x.get('recovery',{}).get('state','complete')=='complete') or
        x['state']=='strict-ungraded' or x['state']=='skipped' and x['reason']=='Scripted clarification reply was not requested' for x in report['checks']) else 'partial')
    report['counts'] = {v:sum(x.get('verdict')==v for x in report['checks']) for v in ('pass','fail')}
    report['counts']['ungraded'] = sum(x.get('verdict') is None and (x['state']=='ungraded' or x['state']=='skipped' and x['reason']=='Dependency unavailable') for x in report['checks'])
    report['counts']['strict_ungraded']=sum(x['state']=='strict-ungraded' for x in report['checks'])
    recovery_ungraded=sum(x.get('recovery',{}).get('state','complete')!='complete' or
        x.get('recovery',{}).get('score_state')=='strict-ungraded' for x in report['checks'])
    if recovery_ungraded:report['counts']['recovery_ungraded']=recovery_ungraded
    save(root / 'evaluation.json', report)
    return report
