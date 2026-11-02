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


def test_check_view_shows_answer_verdict_and_native_readiness():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable')
    root = Path(__file__).resolve().parents[1]
    dashboard = (root/'explorer/dashboard.js').read_text().split('document.addEventListener("click"')[0]
    timeline = (root/'explorer/timeline.js').read_text()
    code = dashboard + '\n' + timeline + r'''
const assert = require('node:assert/strict');
const run = {resolved_spec:{timezone:'America/New_York'}, evaluations:[{id:'later', contract:{question:'Where?',claims:[{id:'x',statement:'At the cafe'}]}}]};
const status = checkStatus(run,{id:'later',at:'2026-10-26T10:00:00-04:00',state:'ungraded',write_readiness:{state:'unavailable',latency_s:901,accepted_ids:['doc-1'],dependencies:['write'],native_states:[{id:'doc-1',status:'done',dreaming_status:'dreaming'}],reason:'deadline exceeded'}});
assert.match(status,/Question not asked · ungraded/); assert.match(status,/doc-1/); assert.match(status,/dreaming/); assert.match(status,/15 minutes/); assert.match(status,/At the cafe/);
const ex={check_id:'later',turn:{prompt:'Where?',at:'2026-10-26T10:00:00-04:00',answer:'At the park',tool_calls:[{name:'search',input:{query:'place'}}],tool_results:[{text:'cafe result',success:true}]}, contract:{claims:[{id:'x',statement:'At the cafe'}]},grade:{claims:[{id:'x',verdict:'misrepresented',reason:'says park'}],evidence_diagnostic:{state:'complete',claims:[{id:'x',status:'supported',citations:[{id:'tool-result-1',quote:'cafe result'}]}],extra_assertions:[]}}};
const html=providerExchange(run,{},ex,0); assert.match(html,/At the park/); assert.match(html,/Failed · wrong/); assert.match(html,/cafe result/); assert.match(html,/availability does not prove/);
selectedProvider='supermemory-repeat-1';
run.arms={'supermemory-repeat-1':{label:'Supermemory',exchanges:[{...ex,kind:'initial',check_id:'first'}],evaluation:{checks:[{id:'first',state:'diagnostic'}, {id:'later',at:'2026-10-26T10:00:00-04:00',state:'ungraded',write_readiness:{state:'unavailable',latency_s:901,accepted_ids:['doc-1'],native_states:[{id:'doc-1',status:'done',dreaming_status:'dreaming'}]}}]}}};
const judge=judgePage(run); assert.ok(judge.indexOf('At the park') < judge.indexOf('Question not asked · ungraded')); assert.match(judge,/doc-1/); assert.match(judge,/dreaming/); assert.match(judge,/At the cafe/);
'''
    result = subprocess.run([node, '-e', code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
