"""Run one deliberately incorrect Pizza Day answer through the published judge."""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import time

from .canary_capability import binding, read
from .live_dispatch import CONDITION, process_identity
from .pizza_day_controller import BASIS, SPEC, inputs
from .semantic_grade import judge_prompt
from .workflow_runner import grade_turn, judge_options

SCOPE = 'pizza-day-wrong-answer-judge-v1'
NAME = 'judge-control'
PROJECT = 'pacbench-pizza-day-control-judge'
ANSWER = "Your pizza dinner is with Maya tomorrow, Saturday September 12, at Tony's on 8th at 9pm."


def control_input(preparation):
    _, _, turn = inputs(preparation)
    surface = dict(answer=ANSWER, history=[dict(role='user', content=turn.prompt)], conversation_times=[])
    return turn, surface


def validate(scope, bound):
    root = Path(scope['root'])
    if (root != root.resolve() or root.name != NAME or scope['provider'] != NAME
            or scope['run_id'] != root.parent.name or scope['frozen_inputs'] != BASIS
            or scope['spec_sha256'] != SPEC or Path(bound['path']) != root/'workflow-scope.json'
            or scope['projects'] != {NAME: {'judge': PROJECT}} or scope['controller'] != process_identity()):
        raise ValueError('wrong-answer control requires its own judge-only scope')


def admit(scope, project, body):
    from .openrouter_route import with_route
    from .workflow_capability import ENV, PROFILE
    turn, surface = control_input(Path(scope['preparation']))
    prompt = judge_prompt(turn.prompt, ANSWER, turn.claims, **judge_options(turn, surface))
    expected = with_route(dict(model='openai/gpt-6-luna', messages=[dict(role='user', content=prompt)],
        stream=True, stream_options={'include_usage':True}, max_completion_tokens=32768,
        response_format={'type':'json_object'}), profile=PROFILE)
    if project != PROJECT or body != expected:
        raise ValueError('unexpected calibration request')
    return dict(root=scope['root'], capability=json.loads(os.environ[ENV]), run_id=scope['run_id'],
        slot=dict(kind='judge', provider=NAME, question_id=turn.id), projects=[project])


def main():
    from . import workflow_capability as cap
    from .gateway_client import client_settings
    from .model_calls import ModelClient
    from .observational_receipts import credit_snapshot
    from .openrouter_direct import direct_environment
    from .openrouter_route import CHAT_COMPLETIONS_URL, MODEL, PROFILE_NAME
    from .openrouter_run import fetch_catalog
    from .workflow_receipts import receipt_summary
    from .workflow_memcal_live import run_environment

    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ('campaign', 'preparation', 'confirmation'):
        parser.add_argument(arg, type=Path)
    args = parser.parse_args()
    root = args.campaign.resolve()/NAME
    turn, surface = control_input(args.preparation)
    proof = binding(args.confirmation.resolve())
    cap.confirm_setting(read(proof))
    if PROFILE_NAME != cap.PROFILE:
        raise ValueError('Luna profile required before startup')
    fetch_catalog(profile=cap.PROFILE)
    root.mkdir(mode=0o700, exist_ok=False)
    settings = dict(PACBENCH_RECEIPT_JOURNAL=str(root/'receipts.jsonl'), PACBENCH_ROLE_ROUTES='luna-all-roles-v1',
        PACBENCH_LUNA_COMPATIBILITY='temperature-omitted-default-v1', PACBENCH_EFFECTIVE_OUTPUT_TOKENS='32768',
        PACBENCH_DISPATCH_TRACKING=CONDITION, PACBENCH_STOP_FILE=str(root/'STOP'),
        PACBENCH_OBSERVATIONAL_COHORT_STOP=str(root.parent/'ACCOUNT_STOP'))
    with run_environment(settings):
        scope = dict(root=str(root), admission=SCOPE, provider=NAME, run_id=root.parent.name, frozen_inputs=BASIS,
            spec_sha256=SPEC, preparation=str(args.preparation.resolve()), projects={NAME:{'judge':PROJECT}},
            controller=process_identity(), prepaid=credit_snapshot(), auto_recharge_evidence=proof)
        path = root/'workflow-scope.json'
        with path.open('x') as f:
            json.dump(scope, f)
        with run_environment({cap.ENV:json.dumps(binding(path))}):
            ledger = cap.budget()
            report = dict(state='ungraded', condition=SCOPE)
            started = time.monotonic()
            try:
                with direct_environment(os.environ['OPENROUTER_API_KEY']):
                    _, key = client_settings(PROJECT)
                    model = ModelClient(key=key, model=MODEL, record_dir=root/'model-calls', url=CHAT_COMPLETIONS_URL,
                                        json_mode=True, max_attempts=1)
                    grade = grade_turn(turn, surface, model, root/'judgments', require_receipt=True)
                    report.update(state='graded', answer=ANSWER, grade=asdict(grade))
            finally:
                report.update(elapsed_s=time.monotonic()-started, billing=receipt_summary(root, ledger.snapshot(), root.parent.name))
                (root/'result.json').write_text(json.dumps(report, indent=2))
            print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
