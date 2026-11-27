import json

from pathlib import Path

import pytest

def test_retained_drained_answers_recover_without_source_mutation(tmp_path):
 from pacbench.judging.overlays import recover
 from pacbench.harness import TurnResult
 from pacbench.workflows.turn_evidence import audit_transport
 root=Path(__file__).resolve().parents[1]/'runs/revised-r2-observational-live-20261117/remaining-native/mem0'
 files=list(root.glob('checkpoint-*/reuse-turns/*/answer-ready.json'))
 if not files:pytest.skip('Private retained captures unavailable')
 for i,p in enumerate(files):
  before=p.read_bytes();out=tmp_path/(str(i)+'.json');recover(p.parent,out)
  audit_transport(TurnResult(**json.loads(out.read_text())))
  assert p.read_bytes()==before
