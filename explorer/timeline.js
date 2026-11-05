"use strict";

let selectedDay = "";
let selectedProvider = "";
const dayFormatters = new Map();
const timeFormatters = new Map();
const sourceIndexes = new WeakMap();
const readableDayFormatter = new Intl.DateTimeFormat("en-US", {timeZone:"UTC",weekday:"long",month:"short",day:"numeric"});

function zoneOf(run) { return run.resolved_spec?.timezone || run.spec?.timezone || ""; }
function dayKey(value, run) {
  if (!value) return "";
  const zone = zoneOf(run);
  if (zone && String(value).includes("T") && /(?:Z|[+-]\d\d:\d\d)$/.test(value)) {
    const stamp = new Date(value);
    if (!Number.isNaN(stamp.valueOf())) {
      try {
        if (!dayFormatters.has(zone)) dayFormatters.set(zone,
          new Intl.DateTimeFormat("en-US", {timeZone: zone, year:"numeric", month:"2-digit", day:"2-digit"}));
        const parts = dayFormatters.get(zone).formatToParts(stamp);
        const part = type => parts.find(p => p.type === type).value;
        return `${part("year")}-${part("month")}-${part("day")}`;
      } catch { /* Preserve the saved local date if a historical zone is unavailable. */ }
    }
  }
  return String(value).slice(0, 10);
}
function readableDay(value) {
  if (!/^\d{4}-\d\d-\d\d$/.test(value)) return "Date not captured";
  return readableDayFormatter.format(new Date(value + "T12:00:00Z"));
}
function localClock(value, run) {
  if (!value) return "Time not captured";
  if (zoneOf(run) && /(?:Z|[+-]\d\d:\d\d)$/.test(value)) {
    try {
      const zone = zoneOf(run);
      if (!timeFormatters.has(zone)) timeFormatters.set(zone,
        new Intl.DateTimeFormat("en-US", {timeZone:zone,hour:"numeric",minute:"2-digit"}));
      return timeFormatters.get(zone).format(new Date(value));
    } catch { /* saved time below */ }
  }
  return String(value).slice(11,16) || "Time not captured";
}
function exposureAt(item) { return item.record?.processing_at || item.at || item.record?.at || ""; }
function simulationDays(run) {
  const days = new Set(sources(run).map(m => dayKey(m.written_at, run)).filter(Boolean));
  Object.values(run.arms || {}).forEach(arm => {
    (arm.exposures || []).forEach(item => { const day = dayKey(exposureAt(item),run); if(day) days.add(day); });
    (arm.exchanges || []).forEach(ex => { const day = dayKey(ex.turn?.at || ex.day,run); if(day) days.add(day); });
  });
  (run.evaluations || []).forEach(check => { const day=dayKey(check.at,run); if(day) days.add(day); });
  const questionDay = dayKey(run.resolved_spec?.question_at,run);
  if(questionDay) days.add(questionDay);
  return [...days].sort();
}
function eventsOnDay(arm, date, run) {
  const captured = new Set((arm.exchanges || []).map(item => item.check_id));
  const events = [
    ...(arm.exposures || []).map((item,index) => ({kind:"ingestion",at:exposureAt(item),item,index})),
    ...(arm.exchanges || []).map((item,index) => ({kind:"recall",at:item.turn?.at || item.day || "",item,index})),
    ...(arm.evaluation?.checks || []).filter(item => !captured.has(item.id))
      .map(item => ({kind:"status",at:item.at,item}))
  ];
  return events.filter(event => dayKey(event.at,run)===date).sort((a,b) => {
    const left=Date.parse(a.at),right=Date.parse(b.at);
    return Number.isFinite(left) && Number.isFinite(right) ? left-right : String(a.at).localeCompare(String(b.at));
  });
}
function checkStatus(run, item) {
  const planned = (run.evaluations || []).find(check => check.id === item.id);
  const readiness = item.write_readiness;
  const criteria = (planned?.contract?.claims || []).map(c => '<div class="verdict-row">' + badge('Ungraded','neutral') +
    '<div><strong>' + esc(c.statement) + '</strong></div></div>').join('');
  const states = readiness?.native_states || [];
  const observed = states.map(s => '<li>' + esc(s.id || 'ID not captured') + ': native status ' + esc(s.status ?? 'unknown') +
    ' · dreaming ' + esc(s.dreaming_status ?? 'unknown') + '</li>').join('');
  const delay = Number.isFinite(readiness?.latency_s) ? Math.round(readiness.latency_s / 60 * 10) / 10 + ' minutes' : 'duration not captured';
  const readinessText = readiness ? '<p>Dependent write readiness: ' + esc(readiness.state || 'unknown') + ' after ' + esc(delay) + '.</p>' +
    (readiness.accepted_ids?.length ? '<p>Accepted native IDs: ' + readiness.accepted_ids.map(esc).join(', ') + '</p>' : '<p>Accepted native IDs: not captured.</p>') +
    (observed ? '<ul>' + observed + '</ul>' : '<p>Native processing status not captured.</p>') +
    (readiness.reason ? '<p>' + esc(readiness.reason) + '</p>' : '') +
    (readiness.dependencies?.length ? '<p>Depends on: ' + readiness.dependencies.map(esc).join(', ') + '</p>' : '') +
    recordButton('Load exact readiness record', readiness.record_url) :
    '<p>' + esc(item.reason || 'No answer recorded.') + (item.cause ? ' · Depends on ' + esc(item.cause) : '') + '</p>';
  return '<section class="exchange check-status"><div class="exchange-label">' + esc(item.id) + ' · ' +
    esc(localClock(item.at,run)) + '</div>' + badge(item.state === 'skipped' && item.reason === 'Scripted clarification reply was not requested' ? 'Question not asked · skipped' : 'Question not asked · ungraded','warn') +
    (planned?.contract?.question ? '<h4>Planned question</h4><p>' + esc(planned.contract.question) + '</p>' : '') +
    readinessText + (criteria ? '<h4>Expected facts</h4>' + criteria : '') + '</section>';
}
function turnRecord(turn) {
  return details("Exact prompt, clock and session", '<h4>Prompt sent to Hermes</h4><pre>' + esc(turn.prompt || "not captured") +
    '</pre><h4>Session and execution</h4>' + dump({at:turn.at,session_id:turn.session_id,
      latency_s:turn.latency_s,returncode:turn.returncode,policy_blocks:turn.policy_blocks,
      context_isolation:turn.context_isolation,
      session_input_tokens:turn.session_input_tokens,session_output_tokens:turn.session_output_tokens}) +
    (turn.returncode ? details("Execution errors", '<pre>' + esc(turn.stderr_full || turn.stderr_tail || "not captured") + '</pre>') : ""));
}
function ingestionExchange(run, arm, item) {
  if (!sourceIndexes.has(run)) sourceIndexes.set(run,new Map(sources(run).map(m=>[m.source_id,m])));
  const packet = item.packet || (item.source_ids || []).map(id=>sourceIndexes.get(run).get(id)).filter(Boolean);
  const document = item.document ?? run.source_documents?.[item.label];
  const delivered = packet.length;
  const packetChat = packet.length ? conversations({...run,messages:packet,observations:[]}) : notice(item.packet_problem || "Delivered texts were not captured.");
  return '<section class="exchange ingestion-exchange"><div class="exchange-label">Provider ingestion · ' +
    esc(localClock(exposureAt(item),run)) + '</div><h3>' + delivered + ' messages in the ingestion packet for ' + esc(arm.label || arm.provider) + '</h3>' +
    (item.record?.ready===false ? notice('Native ingestion or processing failed. Hermes recall was not run.') : '') +
    (['native-imessage','native-sources'].includes(item.delivery) ? '<p>Native Apple Messages database, contacts and email fixtures → provider readers → normal processing.</p>' : item.delivery==='native-messages' ? '<p>Real source speakers, threads and timestamps → native message-batch API. No fabricated assistant turns.</p>' : item.delivery==='document' ? '<p>Raw daily document → provider ingestion API. No Hermes ingestion turn.</p>' : '<p>Inspect the saved source packet and native acknowledgement.</p>') +
    (['native-imessage','native-sources'].includes(item.delivery) ? '<p class="muted">Source collected ' + esc(readableDay(dayKey(item.at,run))) + ' at ' + esc(localClock(item.at,run)) + '; nightly processing ' + esc(readableDay(dayKey(exposureAt(item),run))) + ' at ' + esc(localClock(exposureAt(item),run)) + '.</p>' : '') +
    details("Delivered texts",packetChat) + (document ? details(item.delivery==='native-messages' ? 'Exact source batch before transport' : 'Exact raw source document','<pre>'+esc(document)+'</pre>') : '') +
    usageDetails(item) + details("Provider packets, acknowledgement and parameters",dump(item.record)) + '</section>';

}
function dayTimeline(run) {
  const days = simulationDays(run);
  if (!days.includes(selectedDay)) selectedDay = days[days.length-1] || "";
  const keys = Object.keys(run.arms || {});
  if (!keys.includes(selectedProvider)) selectedProvider = keys.find(key => (run.arms[key].exposures?.length || run.arms[key].exchanges?.length)) || keys[0] || "";
  const messages = sources(run).filter(m => dayKey(m.written_at,run) === selectedDay);
  const arm = run.arms?.[selectedProvider];
  const events = arm ? eventsOnDay(arm,selectedDay,run) : [];
  const sourceCounts = new Map();
  for (const message of sources(run)) {
    const date = dayKey(message.written_at,run);
    sourceCounts.set(date,(sourceCounts.get(date) || 0)+1);
  }
  const dayButtons = days.map(date => {
    const count = sourceCounts.get(date) || 0;
    const providerEvents = Object.values(run.arms || {}).reduce((total,provider)=>total+eventsOnDay(provider,date,run).length,0);
    return '<button type="button" data-day="' + esc(date) + '" aria-pressed="' + (date===selectedDay) + '" class="day-chip ' +
      (date===selectedDay?'active':'') + '"><strong>' + esc(readableDay(date)) + '</strong><span>' + count + ' texts · ' + providerEvents + ' provider events</span></button>';
  }).join("");
  const providers = keys.map(key => '<button type="button" data-provider="' + esc(key) + '" aria-pressed="' + (key===selectedProvider) +
    '" class="' + (key===selectedProvider?'active':'') + '">' + esc(run.arms[key].label || key) + '</button>').join("");
  const status = arm?.status;
  const blocked = ['blocked','preflight-failed'].includes(status?.state) || status?.capability?.available === false;
  const planned = (run.evaluations || []).filter(check => dayKey(check.at,run)===selectedDay);
  const empty = blocked ? notice('Provider unavailable: ' + (status.reason || status.capability?.execution_blocker || status.capability?.reason || 'setup incomplete')) :
    notice(arm ? 'No ingestion or Hermes interaction recorded for this provider on this day.' : 'Prepared dialogue; no provider has run yet.');
  const texts = messages.length ? conversations({...run,messages,observations:[]}) : notice('No new source texts on this day. Earlier messages may still be recalled below.');
  return '<div class="day-navigation" aria-label="Simulation days">' + dayButtons + '</div><div class="timeline-heading"><div><span class="eyebrow">Simulation day</span><h2>' +
    esc(readableDay(selectedDay)) + '</h2></div><span>' + esc(zoneOf(run) || 'Saved local time') + '</span></div>' +
    '<div class="timeline-links"><a href="#generated-texts">Generated texts</a><a href="#hermes-activity">Memory & Hermes</a></div><div class="day-layout"><section id="generated-texts" class="day-texts"><div class="pane-head"><h3>Generated texts</h3>' + badge(messages.length + ' messages') +
    '</div><p class="muted">The original conversations, grouped by the day they were written.</p>' + texts + '</section>' +
    '<section id="hermes-activity" class="day-activity"><div class="pane-head"><h3>Memory & Hermes</h3>' + badge(events.length + ' provider events') + '</div><div class="provider-switch" aria-label="Memory provider">' + providers +
    '</div>' + (arm ? '<p class="muted">' + 'Daily source batches go through native document or message APIs; Memcal reads the synthetic Messages database. Hermes only handles recall. Native completion and failures are recorded.' + '</p>' : '') +
    (arm?.tool_policy?.version?.startsWith('retained-memory-only-') ? '<p class="muted">Archive and original-message tools are disabled. Native event/page citations remain available.</p>' : '') +
    (events.length ? events.filter(event => event.kind !== 'recall' || event.item.kind !== 'followup' || !events.some(first => first.kind === 'recall' && first.item.kind === 'initial' && first.item.check_id === event.item.check_id)).map(event => event.kind==='ingestion' ? ingestionExchange(run,arm,event.item) : event.kind==='status' ? checkStatus(run,event.item) : providerExchange(run,arm,event.item,event.index,event.item.kind === 'initial' ? events.filter(next => next.kind === 'recall' && next.item.kind === 'followup' && next.item.check_id === event.item.check_id).map(next => next.item) : [])).join('') : empty) +
    (!events.length && planned.length ? details('Planned questions and criteria',renderContract({...run,evaluations:planned})) : '') +
    (arm ? details('Provider setup and run status',dump({preflight:arm.preflight,status:arm.status,tool_policy:arm.tool_policy,model_usage:arm.model_usage,model_usage_after_settlement:arm.model_usage_after_settlement,evaluation_context:run.summary?.evaluation_context})) : '') +
    '</section></div>';
}
