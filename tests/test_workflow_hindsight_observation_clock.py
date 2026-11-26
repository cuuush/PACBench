from datetime import datetime
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from pacbench import checkpoint_clock as clock
from pacbench.python_provider_clock import clock_value_ns, shared_time_ns
from pacbench.campaigns.pizza_week import compile_week


@pytest.mark.parametrize('bounded', [False, True])
def test_native_drain_across_authored_hindsight_followup_gap(tmp_path, monkeypatch, bounded):
    _, _, turns, _ = compile_week('continuing-account')
    before, after = turns[24:26]
    assert (before.id, after.id) == ('weekend-correction', 'weekend-uptake')
    initial_observation = datetime.fromisoformat(before.at).timestamp()
    next_observation = datetime.fromisoformat(after.at).timestamp()
    assert next_observation - initial_observation == 120
    real = 1_800_000_000.0
    monkeypatch.setattr(clock.time, 'time', lambda: real)

    proc = Path('/proc/self/stat').read_text()
    started = int(proc.rsplit(')', 1)[1].split()[19])
    pid = clock.os.getpid()
    home = tmp_path/'hermes-home'
    monkeypatch.setattr('pacbench.hermes_gateway._identity', lambda *_: None)
    checkpoint = clock.CheckpointClock(home, before.at, 'fixture', bounded=bounded)
    env = checkpoint.environment({'PACBENCH_WORKFLOW_CAPTURE':'pizza-week-conversation-v2',
                                  'PACBENCH_WORKFLOW_SCOPE':'offline-test'})
    env['PACBENCH_PYTHON_CLOCK_ROLE'] = 'hermes-cli'
    env['HERMES_HOME'] = str(home)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    adoption = checkpoint.folder/'python-adoptions'
    adoption.mkdir()
    (adoption/f'{pid}.json').write_text(json.dumps({'pid':pid,'process_start_ticks':started,'role':'hermes-gateway'}))
    owner = SimpleNamespace(env=env, home=home, witness={'pid':pid,'start_time':started}, fence=lambda: {})

    def submit(turn):
        descriptor = checkpoint.prepare(owner, turn.prompt, turn.at)
        binding = checkpoint.folder/'bindings'/(descriptor['nonce']+'.json')
        binding.write_text(json.dumps({'question_sha256':descriptor['question_sha256']}))
        clock.release_at_submission()
        return shared_time_ns(checkpoint.path, lambda: round(real*1e9), allow_ceiling=bounded)/1e9

    assert submit(before) == initial_observation
    real += 180  # Synthetic stall exceeding the authored 120-second gap.
    if bounded:
        assert clock.observation_epoch(checkpoint.path, real, env) == initial_observation + 60
        assert submit(after) == next_observation
        with pytest.raises(RuntimeError, match='strictly forward'):
            checkpoint.prepare(owner, after.prompt, after.at)
    else:
        with pytest.raises(RuntimeError, match='cannot rewind to a missed question'):
            submit(after)


def test_ceiling_is_never_accepted_on_ordinary_provider_clock():
    with pytest.raises(ValueError, match='Invalid provider clock ceiling'):
        clock_value_ns('-100.000000 ceiling=1700000000000000000\n', 1_800_000_000_000_000_000)
