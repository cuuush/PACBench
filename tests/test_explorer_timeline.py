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
  exposures:[{at:'2026-09-11T05:00:00Z',delivery:'document',label:'native import'}],
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


def test_large_timeline_reuses_formatters_and_counts_days_in_one_pass():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is not installed')
    source = Path(__file__).resolve().parents[1] / 'explorer/timeline.js'
    code = '''
const assert = require('node:assert/strict');
const OriginalFormatter = Intl.DateTimeFormat;
let constructed = 0;
Intl.DateTimeFormat = function(...args) { constructed++; return new OriginalFormatter(...args); };
const sources = run => run.messages;
const esc = value => String(value);
const notice = esc, badge = esc, details = () => '', conversations = () => '';
''' + source.read_text() + '''
let classified = 0;
const originalDayKey = dayKey;
dayKey = (...args) => { classified++; return originalDayKey(...args); };
const run = {resolved_spec:{timezone:'America/New_York'},messages:Array.from({length:3000},(_,i)=>({
  written_at:`2026-10-${String(1+i%30).padStart(2,'0')}T14:00:00Z`}))};
const html = dayTimeline(run);
assert.equal((html.match(/class="day-chip /g)||[]).length,30);
assert.equal((html.match(/100 texts/g)||[]).length,30);
assert(classified < run.messages.length * 4, `Repeated full-corpus day scans: ${classified}`);
for(let i=0;i<100;i++) assert.equal(localClock('2026-10-01T14:00:00Z',run),'10:00 AM');
assert.equal(constructed,3,'One formatter per purpose and zone, independent of message count');
assert.equal(dayKey('2026-11-01T04:30:00Z',run),'2026-11-01');
assert.equal(dayKey('2026-11-01T05:30:00Z',run),'2026-11-01');
'''
    result = subprocess.run([node,'-e',code],capture_output=True,text=True,timeout=15)
    assert result.returncode == 0, result.stderr
