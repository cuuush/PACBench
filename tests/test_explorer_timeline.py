"""Run the timeline's date/ordering logic in Node; synthetic activity is not a live trial."""
import shutil
import subprocess
from pathlib import Path
import pytest


def test_timeline_uses_scenario_timezone_and_includes_activity_only_days():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable')
    source = Path(__file__).resolve().parents[1] / 'explorer/timeline.js'
    code = source.read_text() + r'''
const assert = require('node:assert/strict');
const sources = run => run.messages;
const run = {resolved_spec:{timezone:'America/New_York'},messages:[
  {written_at:'2026-09-11T01:30:00Z'}], arms:{memory:{
  exposures:[{turn:{at:'2026-09-11T05:00:00Z'},label:'catch-up'}],
  exchanges:[{turn:{at:'2026-09-12T13:00:00Z'},kind:'initial'},
             {turn:{at:'2026-09-12T09:00:00-04:00'},kind:'followup'}]}}};
assert.equal(dayKey(run.messages[0].written_at,run),'2026-09-10');
assert.deepEqual(simulationDays(run),['2026-09-10','2026-09-11','2026-09-12']);
assert.equal(eventsOnDay(run.arms.memory,'2026-09-11',run)[0].kind,'ingestion');
assert.deepEqual(eventsOnDay(run.arms.memory,'2026-09-12',run).map(e=>e.item.kind),['initial','followup']);
assert.equal(localClock('2026-09-11T01:30:00Z',run),'9:30 PM');
assert.equal(dayKey('2026-09-11',{resolved_spec:{}}),'2026-09-11');
assert.equal(dayKey(null,run),'');
'''
    result = subprocess.run([node, '-e', code],capture_output=True,text=True)
    assert result.returncode == 0, result.stderr
