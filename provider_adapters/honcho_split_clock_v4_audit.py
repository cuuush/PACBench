"""Read-only verifier for the historical Honcho split-clock v4 condition."""
from datetime import datetime
from pathlib import Path
import hashlib,json
from pacbench.provider_clock import LEGACY_LIBRARY_SHA256 as LIBRARY_SHA256
VERSION='honcho-wall-clock-v4'
SUPPORTED=(VERSION,)
CONFIG='provider-clock.json'
START_TOLERANCE_S=5

def load(root):
    config=json.loads((root/CONFIG).read_text())
    if config['version']!=VERSION or config['library_sha256']!=LIBRARY_SHA256:
        raise RuntimeError('Unrecognized historical split clock')
    return None,config

def verify_adoption(root, value, *, role):
    _, config = load(root)
    expected = config['python_clock']
    if (value.get('protocol') != expected['protocol'] or value.get('version') != expected['version'] or
            value.get('python_sha256') != expected['python_sha256'] or
            value.get('extension_sha256') != expected['extensions_by_abi'].get(value.get('abi')) or
            value.get('bootstrap_sha256') != expected['bootstrap_sha256'] or
            value.get('clock_file') != str(root.resolve()/'provider-clock/offset') or
            value.get('os_clock') != 'real' or value.get('tls_clock') != 'real' or value.get('role') != role):
        raise RuntimeError('Python provider clock adoption differs from declared runtime')
    return value

def audit_evidence(root, declared):
    """Offline/publication validation; it does not need a running native DB."""
    errors = []
    def check(value, message):
        if not value: errors.append(message)
    def instant(value):
        result = datetime.fromisoformat(value)
        if result.tzinfo is None: raise ValueError('clock evidence lacks timezone')
        return result
    try:
        config = json.loads((root/CONFIG).read_text())
        startup = json.loads((root/'provider-clock-startup.json').read_text())
        running_overhead=root/'provider-clock-running-overhead.jsonl'
        if running_overhead.exists():
            check(config.get('overhead_fallback')=='native-clock-runs; no rewind; scripted checkpoint gates remain enforced',
                  'Honcho unexcluded judging overhead condition is undeclared')
            for line in running_overhead.read_text().splitlines():
                record=json.loads(line)
                real_before,real_after=instant(record['real_started_at']),instant(record['real_finished_at'])
                provider_before,provider_after=instant(record['provider_started_at']),instant(record['provider_finished_at'])
                check(record.get('version')=='native-running-overhead-v1' and record.get('clock_protocol')==declared and
                      record.get('reason') in {'native-work-active','container-freezer-unavailable'} and
                      record.get('state')=='observed' and record.get('database_paused') is False and
                      record.get('native_clock_running') is True and record.get('offset_adjustment') is False and
                      len(record.get('offset_before_sha256',''))==64 and
                      record['offset_before_sha256']==record.get('offset_after_sha256') and
                      real_before<=real_after and provider_before<=provider_after and
                      abs(((provider_after-provider_before)-(real_after-real_before)).total_seconds())<=3 and
                      abs((instant(record['db_started_at'])-provider_before).total_seconds())<=3 and
                      abs((instant(record['db_finished_at'])-provider_after).total_seconds())<=3,
                      'Honcho unexcluded judging overhead clock witness is inconsistent')
                if record.get('reason')=='container-freezer-unavailable':
                    failure=record.get('pause_failure') or {}
                    check(failure.get('returncode') and 'freezer not supported' in failure.get('error','').lower() and
                          hashlib.sha256(failure.get('error','').encode()).hexdigest()==failure.get('error_sha256') and
                          failure.get('inspection')=={'Running':True,'Paused':False},
                          'Honcho unsupported-freezer fallback lacks a safe native state witness')
        check(declared in SUPPORTED and config['version'] == startup['version'] == declared,
              'Honcho clock condition/version mismatch')
        check(config['library_sha256'] == startup['library_sha256'] == LIBRARY_SHA256 and
              config['wall_speed'] == 1 and config['monotonic'] == startup['monotonic'] == 'real' and
              config['gateway_clock'] == 'real' and config['source_timestamps'] == 'unchanged' and
              config['native_batching'] == 'unchanged', 'Honcho clock isolation declaration differs')
        provider_at = instant(startup['provider_at'])
        check(abs((instant(startup['api_date'])-provider_at).total_seconds()) <= 5 and
              abs((instant(startup['db_at'])-provider_at).total_seconds()) <= 5,
              'Honcho native API/database clocks disagree')
        previous = provider_at
        previous_real = instant(startup['real_at'])
        steps = (root/'provider-clock-steps.jsonl').read_text().splitlines()
        check(bool(steps), 'Honcho clock has no observed advances')
        for line in steps:
            step = json.loads(line)
            if step.get('kind') == 'benchmark-overhead':
                frozen = instant(step['provider_at']); real = instant(step['real_at'])
                check(declared in ('honcho-wall-clock-v3', VERSION) and step['version'] == declared and
                      step['database_paused'] is True and previous <= frozen and
                      previous_real <= instant(step['real_started_at']) <= real and
                      abs((instant(step['db_after'])-frozen).total_seconds()) <= 3 and
                      all(step['pre_step'].get(k)==0 for k in ('claims','eligible','embeddings','busy_connections')),
                      'Honcho benchmark-overhead suspension evidence is inconsistent')
                previous, previous_real = instant(step['db_after']), real
                continue
            before, after, target = (instant(step[k]) for k in ('from','to','target'))
            real = instant(step['real_at'])
            check(step['version'] == declared and previous <= before <= target <= after and
                  previous_real <= real and (after-target).total_seconds() <= 3,
                  'Honcho clock advance is inconsistent or moves backward')
            check(abs((instant(step['db_after'])-after).total_seconds()) <= 3,
                  'Honcho database did not witness clock advance')
            state = step['pre_step']
            check(all(state.get(k) == 0 for k in ('claims','eligible','embeddings','busy_connections')),
                  'Honcho clock advanced with active observed work')
            if state.get('errors'):
                check({e['id'] for e in step.get('native_errors_before',[])} >=
                      {e['id'] for e in state['errors']},
                      'Honcho native errors were not preserved before aging')
            if declared in ('honcho-wall-clock-v2','honcho-wall-clock-v3', VERSION) and step.get('horizon'):
                check(target <= instant(step['horizon']), 'Honcho clock advanced past checkpoint horizon')
            previous, previous_real = after, real
        if declared == VERSION:
            check(config['python_clock']['tls_clock']=='real' and config['python_clock']['os_clock']=='real',
                  'Honcho split clock declaration differs')
            for role,pid in config['processes'].items():
                check(startup['python_adoptions'][role]['pid']==pid,'Honcho native Python clock PID differs')
                verify_adoption(root,startup['python_adoptions'][role],role=role)
        if declared in ('honcho-wall-clock-v2','honcho-wall-clock-v3', VERSION):
            for path in (root/'turns').glob('**/turn.json'):
                turn = json.loads(path.read_text())
                observed = turn.get('provider_clock') or {}
                if declared == VERSION:
                    adoption=observed.get('python_clock') or {}
                    check(adoption.get('state')=='pass','Honcho turn lacks verified Python clock adoption')
                    verify_adoption(root,adoption['adoption'],role='hermes')
                check(observed.get('version') == declared, 'Honcho turn lacks native clock observation')
                actual = instant(observed['actual_start_at'])
                requested = instant(observed['requested_at'])
                if observed.get('role') == 'scripted':
                    check(turn['at'] == observed['requested_at'] and
                          0 <= (actual-requested).total_seconds() <= START_TOLERANCE_S,
                          'Honcho scripted turn used future native time')
                elif observed.get('role') == 'recovery':
                    check(requested <= instant(turn['at']) <= actual and
                          (actual-instant(turn['at'])).total_seconds() <= START_TOLERANCE_S,
                          'Honcho recovery clock is inconsistent')
                    if observed.get('next_checkpoint'):
                        check(actual < instant(observed['next_checkpoint']),
                              'Honcho recovery crossed next scripted checkpoint')
                else:
                    errors.append('Honcho turn clock role is unknown')
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        errors.append('Honcho native clock evidence missing or invalid: '+type(exc).__name__)
    return errors
