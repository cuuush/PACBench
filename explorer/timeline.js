"use strict";

let selectedDay = "";
let selectedProvider = "";

function zoneOf(run) { return run.resolved_spec?.timezone || run.spec?.timezone || ""; }
function dayKey(value, run) {
  if (!value) return "";
  const zone = zoneOf(run);
  if (zone && String(value).includes("T") && /(?:Z|[+-]\d\d:\d\d)$/.test(value)) {
    const stamp = new Date(value);
    if (!Number.isNaN(stamp.valueOf())) {
      try {
        const parts = new Intl.DateTimeFormat("en-US", {timeZone: zone, year:"numeric", month:"2-digit", day:"2-digit"}).formatToParts(stamp);
        const part = type => parts.find(p => p.type === type).value;
        return `${part("year")}-${part("month")}-${part("day")}`;
      } catch { /* Preserve the saved local date if a historical zone is unavailable. */ }
    }
  }
  return String(value).slice(0, 10);
}
function readableDay(value) {
  if (!/^\d{4}-\d\d-\d\d$/.test(value)) return "Date not captured";
  return new Intl.DateTimeFormat("en-US", {timeZone:"UTC",weekday:"long",month:"short",day:"numeric"}).format(new Date(value + "T12:00:00Z"));
}
function localClock(value, run) {
  if (!value) return "Time not captured";
  if (zoneOf(run) && /(?:Z|[+-]\d\d:\d\d)$/.test(value)) {
    try { return new Intl.DateTimeFormat("en-US", {timeZone:zoneOf(run),hour:"numeric",minute:"2-digit"}).format(new Date(value)); } catch { /* saved time below */ }
  }
  return String(value).slice(11,16) || "Time not captured";
}
function exposureAt(item) { return item.turn?.at || item.at || item.record?.at || ""; }
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
  const events = [
    ...(arm.exposures || []).map((item,index) => ({kind:"ingestion",at:exposureAt(item),item,index})),
    ...(arm.exchanges || []).map((item,index) => ({kind:"recall",at:item.turn?.at || item.day || "",item,index}))
  ];
  return events.filter(event => dayKey(event.at,run)===date).sort((a,b) => {
    const left=Date.parse(a.at),right=Date.parse(b.at);
    return Number.isFinite(left) && Number.isFinite(right) ? left-right : String(a.at).localeCompare(String(b.at));
  });
}
function turnRecord(turn) {
  return details("Exact prompt, clock and session", '<h4>Prompt sent to Hermes</h4><pre>' + esc(turn.prompt || "not captured") +
    '</pre><h4>Session and execution</h4>' + dump({at:turn.at,session_id:turn.session_id,
      latency_s:turn.latency_s,returncode:turn.returncode,policy_blocks:turn.policy_blocks,
      session_input_tokens:turn.session_input_tokens,session_output_tokens:turn.session_output_tokens}) +
    (turn.returncode ? details("Execution errors", '<pre>' + esc(turn.stderr_full || turn.stderr_tail || "not captured") + '</pre>') : ""));
}
function ingestionExchange(run, arm, item) {
  const turn = item.turn;
  const packet = item.packet || [];
  const delivered = packet.length;
  const packetChat = packet.length ? conversations({...run,messages:packet,observations:[]}) : notice(item.packet_problem || "Delivered texts were not captured.");
  if (!turn) return '<section class="exchange ingestion-exchange"><div class="exchange-label">Provider ingestion · ' +
    esc(localClock(exposureAt(item),run)) + '</div><h3>' + delivered + ' messages in the ingestion packet for ' + esc(arm.label || arm.provider) + '</h3>' +
    (item.record?.ready===false ? notice('Native ingestion or processing failed. Hermes recall was not run.') : '') +
    (item.delivery==='native-imessage' ? '<p>Native Apple Messages database and separate contacts fixture → provider reader → normal processing.</p>' : item.delivery==='document' ? '<p>Raw daily document → provider ingestion API. No Hermes ingestion turn.</p>' : '<p>Historical adapter ingestion. Inspect the saved packet and acknowledgement for its delivery method.</p>') +
    details("Delivered texts",packetChat) + (item.document ? details('Exact raw source document','<pre>'+esc(item.document)+'</pre>') : '') +
    usageDetails(item) + details("Provider packets, acknowledgement and parameters",dump(item.record)) + '</section>';
  const failed = turn.returncode || turn.policy_blocks?.length || (typeof turn.policy_blocks === "number" && turn.policy_blocks > 0);
  return '<section class="exchange ingestion-exchange"><div class="exchange-label">Catch-up · ' + esc(localClock(turn.at,run)) +
    ' · ' + esc(item.label) + '</div><div class="chat-row mine"><div class="chat-message"><div class="bubble">' +
    esc(item.packet_verified ? 'Here are my recent messages. Catch up on them for me.' : questionText(turn,{})) +
    (item.packet_verified ? '<div class="attachment">' + delivered + ' delivered texts</div>' : '') +
    '</div></div></div>' + (failed ? notice('This ingestion turn failed or was blocked. Delivery does not establish retention.') : '') +
    details("Texts delivered in this turn · " + (item.packet_verified ? delivered : "unverified"),packetChat) +
    '<div class="tool-sequence">' + toolCards(run,arm,turn) + '</div>' +
    '<div class="chat-row theirs"><span class="avatar hermes-avatar">H</span><div class="chat-message"><div class="sender">Hermes</div>' +
    '<div class="bubble answer-bubble">' + esc(turn.answer || 'No answer captured.') + '</div></div></div>' +
    (turn.prefetch_context ? details("Memory brief visible for this turn",'<pre>' + esc(turn.prefetch_context) + '</pre>') : '') +
    usageDetails(item) + turnRecord(turn) + '</section>';
}
function dayTimeline(run) {
  const days = simulationDays(run);
  if (!days.includes(selectedDay)) selectedDay = days[0] || "";
  const keys = Object.keys(run.arms || {});
  if (!keys.includes(selectedProvider)) selectedProvider = keys.find(key => (run.arms[key].exposures?.length || run.arms[key].exchanges?.length)) || keys[0] || "";
  const messages = sources(run).filter(m => dayKey(m.written_at,run) === selectedDay);
  const arm = run.arms?.[selectedProvider];
  const events = arm ? eventsOnDay(arm,selectedDay,run) : [];
  const dayButtons = days.map(date => {
    const count = sources(run).filter(m => dayKey(m.written_at,run) === date).length;
    const turns = Object.values(run.arms || {}).reduce((total,provider)=>total+eventsOnDay(provider,date,run).length,0);
    return '<button type="button" data-day="' + esc(date) + '" aria-pressed="' + (date===selectedDay) + '" class="day-chip ' +
      (date===selectedDay?'active':'') + '"><strong>' + esc(readableDay(date)) + '</strong><span>' + count + ' texts · ' + turns + ' turns</span></button>';
  }).join("");
  const providers = keys.map(key => '<button type="button" data-provider="' + esc(key) + '" aria-pressed="' + (key===selectedProvider) +
    '" class="' + (key===selectedProvider?'active':'') + '">' + esc(run.arms[key].label || key) + '</button>').join("");
  const status = arm?.status;
  const blocked = status?.state === 'blocked' || status?.capability?.available === false;
  const planned = (run.evaluations || []).filter(check => dayKey(check.at,run)===selectedDay);
  const empty = blocked ? notice('Provider unavailable: ' + (status.reason || status.capability?.execution_blocker || status.capability?.reason || 'setup incomplete')) :
    notice(arm ? 'No ingestion or Hermes interaction recorded for this provider on this day.' : 'Prepared dialogue; no provider has run yet.');
  const texts = messages.length ? conversations({...run,messages,observations:[]}) : notice('No new source texts on this day. Earlier messages may still be recalled below.');
  return '<div class="day-navigation" aria-label="Simulation days">' + dayButtons + '</div><div class="timeline-heading"><div><span class="eyebrow">Simulation day</span><h2>' +
    esc(readableDay(selectedDay)) + '</h2></div><span>' + esc(zoneOf(run) || 'Saved local time') + '</span></div>' +
    '<div class="timeline-links"><a href="#generated-texts">Generated texts</a><a href="#hermes-activity">Memory & Hermes</a></div><div class="day-layout"><section id="generated-texts" class="day-texts"><div class="pane-head"><h3>Generated texts</h3>' + badge(messages.length + ' messages') +
    '</div><p class="muted">The original conversations, grouped by the day they were written.</p>' + texts + '</section>' +
    '<section id="hermes-activity" class="day-activity"><div class="pane-head"><h3>Memory & Hermes</h3>' + badge(events.length + ' turns') + '</div><div class="provider-switch" aria-label="Memory provider">' + providers +
    '</div>' + (arm ? '<p class="muted">' + (run.summary?.ingestion_mode==='documents' ? 'Source documents go directly to the provider; Memcal reads the synthetic Messages database. Hermes only handles recall. Native completion and failures are recorded.' : run.kind==='native' ? 'Historical chat-handoff diagnostic: catch-up delivers texts to Hermes. Delivery alone is not proof of retention.' : 'Provider packets and acknowledgements show ingestion separately from Hermes recall.') + '</p>' : '') +
    (events.length ? events.map(event => event.kind==='ingestion' ? ingestionExchange(run,arm,event.item) : providerExchange(run,arm,event.item,event.index)).join('') : empty) +
    (!events.length && planned.length ? details('Planned questions and criteria',renderContract({...run,evaluations:planned})) : '') +
    (arm ? details('Provider setup and run status',dump({preflight:arm.preflight,status:arm.status,model_usage:arm.model_usage,model_usage_after_settlement:arm.model_usage_after_settlement,exposure_context:run.summary?.exposure_context,evaluation_context:run.summary?.evaluation_context})) : '') +
    '</section></div>';
}
