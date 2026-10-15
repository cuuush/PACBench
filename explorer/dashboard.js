"use strict";

let data = {runs: []};
let current = "";
let page = "timeline";
const el = id => document.getElementById(id);
const esc = value => String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
const dump = value => "<pre>" + esc(JSON.stringify(value, null, 2)) + "</pre>";
const short = value => value ? esc(String(value).slice(0, 12)) : "not captured";
const notice = text => '<div class="notice">' + esc(text) + "</div>";
const details = (title, body, extra = "") => '<details class="fold ' + extra + '"><summary>' + esc(title) + '</summary><div class="fold-body">' + body + "</div></details>";
const badge = (text, tone = "") => '<span class="badge ' + tone + '">' + esc(text) + "</span>";
const idOf = run => run.id || run.dir;
const sources = run => run.messages || run.observations || [];
const sourceLink = id => {
  const match = String(id).match(/-(\d+)$/);
  const label = match ? "Message " + (Number(match[1]) + 1) : "Show message";
  return '<button class="source-link" type="button" data-jump="' + esc(id) + '">' + label + '</button>';
};

function stateOf(run) {
  if (run.audit?.valid_for_comparison === false) return "excluded";
  if (run.audit?.state === "fail") return run.summary?.state === 'incomplete' ? 'incomplete' : 'needs review';
  if (run.summary?.state) return run.summary.state;
  if (run.experiment_status?.state && run.experiment_status.state !== "running")
    return run.experiment_status.state;
  const arms = Object.values(run.arms || {});
  return arms.length && arms.every(arm => arm.status?.state === "valid") ? "complete" : "incomplete";
}
function titleOf(run) {
  const name = run.spec?.id || run.life || idOf(run);
  const words = String(name).replace(/[-_]+/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}
function selected() {
  return data.runs.find(run => idOf(run) === current);
}
function renderRunPicker() {
  el("run-select").innerHTML = data.runs.map(run =>
    '<option value="' + esc(idOf(run)) + '">' + esc(titleOf(run)) + " · " +
    esc(stateOf(run)) + " · " + esc(idOf(run)) + "</option>").join("");
  el("run-select").value = current;
}
function choose(id, nextPage = "timeline", scroll = true) {
  const priorScroll = window.scrollY;
  if (id !== current) { selectedDay = ""; selectedProvider = ""; }
  if (!data.runs.some(run => idOf(run) === id)) return;
  current = id;
  page = nextPage;
  renderRunPicker();
  render();
  history.replaceState(null, "", "?run=" + encodeURIComponent(id) +
    (page === "timeline" ? "" : "&view=" + encodeURIComponent(page)) +
    (selectedDay ? "&day=" + encodeURIComponent(selectedDay) : "") +
    (selectedProvider ? "&provider=" + encodeURIComponent(selectedProvider) : ""));
  const rail = document.querySelector(".day-navigation");
  const active = rail?.querySelector('[aria-pressed="true"]');
  if (active) {
    const offset = active.getBoundingClientRect().left - rail.getBoundingClientRect().left + rail.scrollLeft;
    rail.scrollLeft = Math.max(0, offset - (rail.clientWidth - active.clientWidth) / 2);
  }
  window.scrollTo(0, scroll ? 0 : priorScroll);
}
function nav(items) {
  if (!items.some(item => item[0] === page)) page = "timeline";
  return '<nav class="pages" aria-label="Run pages">' + items.map(item =>
    '<button type="button" data-page="' + esc(item[0]) + '" class="' +
    (page === item[0] ? "active" : "") + '">' + esc(item[1]) + "</button>").join("") + "</nav>";
}
function message(id) {
  return sources(selected()).find(item => item.source_id === id);
}
function clock(value) {
  return esc((value || "").slice(11, 16) || "time not captured");
}
function day(value) {
  return esc((value || "").slice(0, 10) || "date not captured");
}
function chatThread(items) {
  const names = [...new Set(items.filter(item => !item.from_owner).map(item => item.sender))];
  const heading = items[0]?.structured?.chat_name || (names.length ? names.join(", ") : "Conversation");
  let priorDay = "";
  let rows = "";
  for (const item of items) {
    const nextDay = day(item.written_at);
    if (nextDay !== priorDay) rows += '<div class="chat-day">' + nextDay + "</div>";
    const owner = item.from_owner === true;
    rows += '<div class="chat-row ' + (owner ? "mine" : "theirs") +
      '" data-message="' + esc(item.source_id) + '">' +
      (owner ? "" : '<span class="avatar">' + esc((item.sender || "?").slice(0, 1).toUpperCase()) + "</span>") +
      '<div class="chat-message">' + (owner ? "" : '<div class="sender">' + esc(item.sender) + "</div>") +
      '<div class="bubble">' + esc(item.text) + "</div>" +
      '<div class="message-time">' + clock(item.written_at) + "</div></div></div>";
    priorDay = nextDay;
  }
  return '<section class="conversation"><div class="conversation-head"><strong>' + esc(heading) +
    '</strong><span>' + esc(items[0]?.channel || "text") + " · " + items.length +
    ' messages</span></div><div class="chat-window">' + rows + "</div></section>";
}
function emailRead(item) {
  if (!item) return notice("No email captured.");
  const from = item.from_owner ? "You" : item.sender;
  const to = item.from_owner ? (item.structured?.participants || []).filter(name => name !== 'Me').join(', ') || item.thread : "You";
  const subject = item.structured?.email_subject || "(no subject)";
  return '<div data-message="' + esc(item.source_id) + '"><h3>' + esc(subject) +
    '</h3><div class="mail-meta">From: ' + esc(from) + "<br>To: " + esc(to) +
    "<br>Date: " + esc(item.written_at) + '</div><div class="mail-body">' +
    esc(item.text) + "</div></div>";
}
function emailThread(items) {
  return '<section class="conversation"><div class="conversation-head"><strong>Mail</strong><span>' +
    items.length + ' messages</span></div><div class="mail-app"><div class="mail-list">' +
    items.map((item, index) => '<button type="button" data-email="' + esc(item.source_id) +
      '" class="mail-item ' + (index === 0 ? "active" : "") + '"><strong>' +
      esc(item.from_owner ? "You" : item.sender) + '</strong><span>' +
      esc(String(item.text || "").split("\n")[0].slice(0, 75)) +
      '</span><small>' + day(item.written_at) + "</small></button>").join("") +
    '</div><div class="mail-read">' + emailRead(items[0]) + "</div></div></section>";
}
function conversations(run) {
  const groups = new Map();
  for (const item of sources(run)) {
    const key = (item.channel || "text") + "\u0000" + (item.thread || "");
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  }
  if (!groups.size) return notice("Source messages were not captured.");
  return [...groups.values()].map(items => {
    items.sort((a, b) => String(a.written_at).localeCompare(String(b.written_at)));
    return items[0].channel === "email" ? emailThread(items) : chatThread(items);
  }).join("");
}
function modelCall(run, path) {
  const exact = (run.model_calls || []).find(call => call.aliases?.includes(path) || call.file === path);
  if (exact) return exact.record;
  const name = String(path || "").split("/").pop();
  const matches = (run.model_calls || []).filter(call => call.file.split('/').pop() === name);
  return matches.length === 1 ? matches[0].record : undefined;
}
function modelReasoningSummary(call) {
  const summaries = (call?.response?.output || []).filter(item => item.type === "reasoning")
    .flatMap(item => item.summary || []).map(item => item.text || "").filter(Boolean);
  return summaries.length ? summaries.join("\n") : null;
}
function renderModelCall(call) {
  if (!call) return notice("Model call record not captured.");
  const reasoning = modelReasoningSummary(call);
  return '<div class="model-meta">' + esc(call.model || "model not captured") +
    " · " + esc(call.created_at || "time not captured") + " · " +
    esc(call.usage?.total_tokens ?? "tokens not reported") + ' tokens</div>' +
    '<h4>Prompt sent</h4>' + dump(call.request?.input || call.request?.messages || []) +
    '<h4>Output returned</h4><pre>' + esc(call.text || "") + "</pre>" +
    '<p class="muted">' + (reasoning ? "Model reasoning summary returned:" : "No readable reasoning summary was returned by this API call.") +
    "</p>" + (reasoning ? '<pre>' + esc(reasoning) + "</pre>" : "") +
    details("Full model request and response", dump(call));
}
function reviewedLines(run, sceneId, lineNumbers) {
  const scene = (run.scenes?.scenes || []).find(item => item.scene_id === sceneId);
  const items = (scene?.source_ids || []).map(id => sources(run).find(item => item.source_id === id));
  return (lineNumbers || []).map(index => {
    const item = items[index - 1];
    return item ? sourceLink(item.source_id) : '<span class="muted">line ' + esc(index) + " unavailable</span>";
  }).join(" ");
}
function renderSceneReview(run, scene) {
  const reviews = (run.verifications || []).filter(item => item.record?.scene_id === scene.scene_id);
  if (!reviews.length) return notice("Rendering review not captured.");
  return reviews.map(item => {
    const record = item.record || {};
    const verdict = record.verdict || {};
    const facts = record.spec?.facts || [];
    const factRows = (verdict.facts || []).map((fact, index) =>
      '<div class="review-fact"><span>' + badge(fact.supported ? "Established" : "Missing",
        fact.supported ? "ok" : "bad") + '</span><div><strong>' +
      esc(facts[index] || "Fact " + (index + 1)) + '</strong><p>' +
      esc(fact.problem || "Cited in the saved conversation.") + "</p>" +
      reviewedLines(run, scene.scene_id, fact.evidence) + "</div></div>").join("");
    return '<div class="review-card"><div class="review-top"><strong>Review pass · attempt ' +
      esc(record.attempt || "?") + "</strong>" +
      badge(record.passed ? "Accepted" : "Rejected", record.passed ? "ok" : "bad") +
      "</div>" + (verdict.problem ? "<p>" + esc(verdict.problem) + "</p>" : "") +
      (factRows ? '<h4>Authored facts checked against this conversation</h4>' + factRows :
        '<p class="muted">This saved run had no authored fact list. The reviewer checked the scene description instead. Scenario evidence: ' +
        reviewedLines(run, scene.scene_id, verdict.evidence) + "</p>") +
      details("Reviewer prompt and full decision", '<h4>Prompt</h4><pre>' +
        esc(record.judge_prompt || "not captured") + '</pre><h4>Response</h4><pre>' +
        esc(record.judge_response || "not captured") + "</pre>" +
        details("Full review record", dump(record))) + "</div>";
  }).join("");
}
function renderContract(run) {
  const evaluations = run.evaluations?.length ? run.evaluations : [{id: "recall", contract: run.contract}];
  return evaluations.map(check => {
    const contract = check.contract;
    if (!contract) return notice("Criteria not frozen yet.");
    return '<div class="contract-question"><span class="eyebrow">' + esc(check.id) +
      (check.at ? " · " + esc(check.at) : "") + '</span><p>' + esc(contract.question) + "</p></div>" +
      (contract.claims || []).map(claim => '<div class="contract-fact"><span>' +
        esc(claim.statement) + '</span><span>' + (claim.evidence || []).map(sourceLink).join(" ") +
        "</span></div>").join("") + details("Criteria review and hashes", dump(contract));
  }).join("");
}
function semanticStory(run) {
  const plans = run.spec?.scenes || run.resolved_spec?.scenes || [];
  const made = run.scenes?.scenes || [];
  const instructions = plans.map((plan, index) =>
    '<div class="scene-intent"><span class="eyebrow">' +
      esc(plan.day ? plan.day + ' · ' + plan.chat + ' · ' + plan.time :
        'Scene ' + (index + 1)) +
      '</span><p>' + esc(plan.scene) + "</p>" +
      (plan.facts?.length ? '<div class="requested-facts"><strong>Facts that must appear</strong>' +
        plan.facts.map(fact => "<div>• " + esc(fact) + "</div>").join("") + "</div>" :
        '<p class="muted">No authored fact list in this saved run.</p>') +
      (plan.test_intent ? '<p class="muted">Recall intent: ' + esc(plan.test_intent) + "</p>" : "") +
    "</div>").join("");
  const renderAudit = made.map((scene, index) => {
    const paths = scene.record?.generation_paths || [];
    const note = (scene.record?.writer_notes || []).filter(Boolean).at(-1);
    return '<section class="stage"><h2>Rendering · ' +
      esc(plans[index]?.day ? plans[index].day + ' · ' + plans[index].chat :
        'scene ' + (index + 1)) + '</h2>' +
      '<p class="muted">The writer generated the conversation. Message times and source IDs were assigned afterward.</p>' +
      (note ? '<div class="writer-note"><strong>Writer note</strong><p>' + esc(note) + '</p></div>' :
        '<p class="muted">No writer note was saved for this run.</p>') +
      paths.map((path, i) => details("Writer call · attempt " + (i + 1),
        renderModelCall(modelCall(run, path)))).join("") +
      renderSceneReview(run, scene) + "</section>";
  }).join("");
  return '<section class="stage"><h2>What the author asked for</h2>' + instructions + "</section>" +
    '<section class="stage"><h2>Rendered conversation</h2>' + conversations(run) +
      details("Exact frozen bundle", run.bundle ? dump(run.bundle) : notice("Full bundle not captured.")) +
    "</section>" + renderAudit +
    '<section class="stage"><h2>Facts ready for recall</h2><p class="muted">These claims were reviewed against the recall goal and cited dialogue, then frozen before either assistant answered.</p>' +
    renderContract(run) + "</section>";
}
function parseResult(result) {
  let value = result?.text;
  if (typeof value === "string" && value.startsWith("<untrusted_tool_result ")) {
    const start = value.indexOf("\n\n");
    const end = value.lastIndexOf("\n</untrusted_tool_result>");
    if (start >= 0 && end > start) value = value.slice(start + 2, end);
  }
  for (let n = 0; n < 4; n++) {
    if (typeof value === "string") {
      try { value = JSON.parse(value); } catch { break; }
    } else if (value && typeof value === "object" && typeof value.result === "string") {
      value = value.result;
    } else break;
  }
  return value;
}
function resultSources(run, arm, result) {
  const text = String(result?.text || "");
  const ids = sources(run).filter(item => text.includes(item.source_id)).map(item => item.source_id);
  return ids.map(sourceLink).join(" ");
}
function resultBody(run, arm, result) {
  if (!result) return notice("Tool result not captured.");
  const parsed = parseResult(result);
  let body = "";
  if (Array.isArray(parsed?.results)) {
    body = parsed.results.length ?
      parsed.results.map((hit, i) => '<div class="tool-hit"><small>Hit ' + (i + 1) +
        (hit.fact_type ? " · " + esc(hit.fact_type) : "") + '</small><p>' +
        esc(hit.text || hit.content || JSON.stringify(hit)) + "</p></div>").join("") :
      '<p class="muted">No hits returned. ' + esc(parsed.message || "") + "</p>";
  } else {
    body = '<pre>' + esc(typeof parsed === "string" ? parsed : JSON.stringify(parsed, null, 2)) + "</pre>";
  }
  const links = resultSources(run, arm, result);
  return body + (links ? '<div class="source-links">Source messages: ' + links + "</div>" : "") +
    details("Exact tool result", dump(result));
}
function toolName(name) {
  return ({
    "mcp__hindsight__recall": "Hindsight recall",
    "memcal_search_archive": "Memcal archive search",
    "memcal_search_wiki": "Memcal wiki search",
    "memcal_conversation": "Memcal conversation"
  })[name] || name;
}
function toolCards(run, arm, turn) {
  const calls = turn.tool_calls || [];
  const traces = turn.provider_traces || [];
  if (!calls.length && !traces.length) return '<div class="quiet">No tool calls recorded for this answer.</div>';
  const cards = [];
  calls.forEach((call, index) => {
    const result = turn.tool_results?.[index];
    const blocked = result?.policy_blocked || String(result?.text || "").includes("PACBENCH-BLOCKED");
    const nested = Array.isArray(call.input?.calls) ? call.input.calls : [];
    let error = false;
    try { error = Boolean(JSON.parse(result?.text || "{}").error); } catch (_) {}
    const failed = result?.success === false || error;
    const query = call.input?.query || call.input?.line_id ||
      (nested.length ? nested.map(c => toolName(c.name)).join(", ") : "");
    const label = call.name === "tool_call" && nested.length === 1 ?
      toolName(nested[0].name) : toolName(call.name);
    const card = '<details class="tool-card"><summary><span class="tool-icon">⌕</span><span><strong>' +
      esc(label) + '</strong><small>' +
      esc(query || (blocked ? "Blocked" : failed ? "Failed" : "Open output")) +
      '</small></span><span class="tool-state">' + (blocked ? "blocked" : failed ? "failed" : result?.success === true ? "returned" : "not captured") +
      '</span></summary><div class="tool-body"><h4>Arguments</h4>' + dump(call.input || {}) +
      '<h4>What came back</h4>' + resultBody(run, arm, result) + "</div></details>";
    cards.push(card);
  });
  if (traces.length) cards.push(details("Provider internal tasks and tool calls", dump(traces)));
  return cards.join("");
}
function score(ex) {
  if (ex.status?.state === "invalid") return ["Invalid checkpoint", "bad"];
  if (ex.status?.state === "incomplete") return ["Incomplete checkpoint", "warn"];
  const grade = ex.grade;
  if (!grade) return ["Ungraded", "neutral"];
  if (!grade.claims?.length) return ["Ungraded", "neutral"];
  if (ex.retention?.protocol_version === 5) {
    const terminal = {incorrect_answer:'Failed · wrong detail',admitted_retrieval_failure:'Failed · detail unavailable',explicit_request_failed:'Failed · explicitly requested detail missing',no_initial_correct_facts:'Failed · no facts recalled'};
    if (terminal[ex.retention.skip_reason]) return [terminal[ex.retention.skip_reason], 'bad'];
  }
  const wrong = grade.misrepresented ?? grade.claims.filter(c => c.verdict === "misrepresented").length;
  const missed = grade.missed ?? grade.claims.filter(c => c.verdict === "missed").length;
  if (wrong) return [wrong + " wrong detail" +
    (wrong === 1 ? "" : "s"), "bad"];
  if (missed) return [missed + " missed detail" +
    (missed === 1 ? "" : "s"), "warn"];
  return ["All facts correct", "ok"];
}
function scoreDetails(run, ex, expanded = true) {
  const [label, tone] = score(ex);
  const criteria = (ex.contract || run.contract)?.claims || [];
  const verdicts = ex.grade?.claims || criteria.map(c => ({id: c.id, verdict: "ungraded", reason: "Awaiting judge"}));
  const rows = verdicts.map(item => {
    const claim = (ex.contract || run.contract)?.claims?.find(c => c.id === item.id);
    return '<div class="verdict-row">' + badge(item.verdict === "correct" ? "Passed" : item.verdict === "missed" ? "Failed · missed" : item.verdict === "misrepresented" ? "Failed · wrong" : "Ungraded", item.verdict === "correct" ? "ok" : item.verdict === "missed" ? "warn" : item.verdict === "ungraded" ? "neutral" : "bad") + '<div><strong>' +
      esc(claim?.statement || item.id) + '</strong><p>' + esc(item.reason) + '</p>' +
      (claim?.evidence || []).map(sourceLink).join(" ") + (ex.grade?.recovery?.find(r => r.id === item.id) ? '<p>Follow-up assessment: ' + esc(ex.grade.recovery.find(r => r.id === item.id).kind.replaceAll('_',' ')) + ' · ' + esc(ex.grade.recovery.find(r => r.id === item.id).reason) + '</p>' : '') + "</div></div>";
  }).join("");
  return '<details ' + (expanded ? 'open ' : '') + 'class="answer-score ' + tone + '"><summary>' + badge(label, tone) +
    '<span>Criteria and judge verdicts</span></summary><div class="fold-body">' +
    (rows || notice("A complete answer judgment was not captured. This answer is ungraded.")) +
    (ex.judge_review ? details("Judge scope review (original grade unchanged)",
      notice("This review records possible judging or question-scope issues. The original trial grade remains above.") +
      '<h4>Review response</h4><pre>' + esc(ex.judge_review.response) +
      '</pre><h4>Review prompt</h4><pre>' + esc(ex.judge_review.prompt) + '</pre>') : "") +
    (ex.judgment ? details("Answer judge prompt and response", '<p class="muted">Judge: ' + esc(modelCall(run, ex.judgment.model_call)?.model || "model not captured") + "</p>" + '<h4>Prompt</h4><pre>' +
      esc(ex.judgment.prompt || "not captured") + '</pre><h4>Response</h4><pre>' +
      esc(ex.judgment.response || "not captured") + "</pre>") : "") + "</div></details>";
}
function questionText(turn, ex) {
  return String(ex.judgment?.question || turn.prompt || "").replace(/^\[Simulated now:[^\n]+\]\n\n/, "");
}
function providerExchange(run, arm, ex, index) {
  const turn = ex.turn;
  if (!turn) return notice("Hermes turn " + (index + 1) + " not captured.");
  return '<section class="exchange"><div class="exchange-label">' + esc(ex.check_id || 'Question ' + (index + 1)) + (ex.kind === 'followup' ? ' · Recovery attempt' : '') +
    " · " + esc(localClock(turn.at,run)) + " · " + esc(readableDay(dayKey(turn.at,run))) + '</div>' +
    '<div class="chat-row mine"><div class="chat-message"><div class="bubble">' +
      esc(questionText(turn, ex)) + "</div></div></div>" +
    (turn.prefetch_context ? details("Memory brief visible for this turn", '<pre>' + esc(turn.prefetch_context) + "</pre>") : "") + effortDetails(ex) + usageDetails(ex) + '<div class="tool-sequence">' + toolCards(run, arm, turn) + "</div>" +
    '<div class="chat-row theirs"><span class="avatar hermes-avatar">H</span><div class="chat-message">' +
      '<div class="sender">Hermes</div><div class="bubble answer-bubble">' +
      esc(turn.answer || "Answer not captured.") + "</div>" +
      scoreDetails(run, ex, page === "judge") + "</div></div>" + turnRecord(turn) + "</section>";
}
function effortDetails(ex) {
  const m = ex.metrics;
  if (!m) return "";
  const retention = ex.retention?.score;
  const path = m.source_fallback_attempted || m.inherited_source_fallback || retention?.claims?.some(c => c.retention_failed) ? "Source lookup: retention failed" :
    m.unclassified_operations || m.inherited_unclassified_operation || retention?.claims?.some(c => c.path_needs_review) ? "Unknown operation: retention uncredited" : "Retained-memory path";
  return '<p class="muted">' + esc(m.top_level_calls ?? m.tool_calls ?? "?") + ' direct calls · ' +
    esc(m.total_tool_operations ?? m.operations?.length ?? "?") + ' operations · ' +
    esc(m.latency_s ?? m.wall_seconds ?? "unknown") + ' seconds · ' + esc(path) + (retention ? ' · Question retention including recovery: ' + esc(retention.points) + '/' + esc(retention.max_points) : '') + '</p>' + (ex.retention?.skip_reason ? notice('Recovery skipped: ' + ({no_initial_correct_facts:'no expected facts recalled',incorrect_answer:'an expected detail was wrong',admitted_retrieval_failure:'Hermes said it could not find an expected detail',explicit_request_failed:'an explicitly requested detail was missed'}[ex.retention.skip_reason] || ex.retention.skip_reason) + '.') : '') + details("Tool effort and retention accounting", dump({metrics: m, score: ex.retention?.score}));
}
function usageDetails(ex) {
  const usage = ex.usage;
  let rows = "";
  if (usage?.state === 'captured') {
    rows = Object.entries(usage.roles || {}).map(([role,total]) => '<tr><td>' + (role === 'hermes' ? 'Hermes' : 'Memory provider') +
      '</td><td>' + esc(total.model_calls) + '</td><td>' + esc(total.input_tokens ?? 'unknown') + '</td><td>' + esc(total.output_tokens ?? 'unknown') +
      '</td><td>$' + esc(Number(total.accounted_upper_bound_usd).toFixed(6)) + (total.unsettled_calls ? ' · includes reservations' : '') + '</td></tr>').join('');
  }
  const authoring = ex.question_plan?.authoring_calls;
  const benchmark = authoring?.length ? '<h4>Follow-up writer and reviewer</h4><p class="muted">' +
    (ex.question_authoring_reused ? 'Reused wording; these are the original authoring records, not fresh charges for this answer.' : 'Separate benchmark overhead, outside Hermes and the memory provider.') + '</p>' + dump(authoring) : '';
  return details('Model calls and token usage', rows ? '<div class="usage-table"><table><thead><tr><th>Who</th><th>LLM calls</th><th>Input tokens</th><th>Output tokens</th><th>Cost upper bound</th></tr></thead><tbody>' + rows +
    '</tbody></table></div><p class="muted">Per-tool tokens are unknown: the native trace does not link each model request to a specific tool. Background requests may finish later. Local embedding/model tokens are not captured in this gateway table.</p>' + details('Individual model requests and late settlements',dump(usage)) + benchmark :
    notice('Separate Hermes/provider usage was not captured for this turn. Unknown does not mean free.') + benchmark);
}
function judgePage(run) {
  return '<section class="stage"><h2>What we are judging</h2><p>Each question has its own frozen expected facts. Recovery asks only for a silent omission from a broad request. Wrong answers, admitted lookup failures, missed explicitly requested details, and zero recall get no follow-up.</p>' +
    '<p class="muted">Questions: ' + esc(run.evaluation_plan?.context || "isolated") + ' context. Earlier answers can resolve references; they do not earn credit for an omitted fact in a later answer.</p>' +
    renderContract(run) + '</section>' + Object.values(run.arms || {}).map(arm =>
      '<section class="stage"><h2>' + esc(arm.label || arm.provider) + '</h2>' +
      (arm.exchanges || []).map((ex, i) => providerExchange(run, arm, ex, i)).join("") + '</section>').join("");
}
function costPage(run) {
  const review = run.cost_review;
  const money = value => value == null ? 'unknown' : '$' + Number(value).toFixed(6);
  return '<section class="stage"><h2>Observed costs</h2>' +
    '<p>These are costs through each arm’s recorded cutoff. Deferred processing is unknown unless inspected; these figures do not establish a full lifecycle price ranking.</p>' +
    '<div class="usage-table"><table><thead><tr><th>Provider</th><th>Initial pass / fail / ungraded</th><th>Retention</th><th>Hermes</th><th>Provider internals</th><th>Held reservations</th><th>Model calls</th><th>Workload complete</th><th>Deferred work</th></tr></thead><tbody>' +
    review.providers.map(row => {
      const h = row.cost?.hermes, p = row.cost?.memory_provider, counts = row.initial_counts;
      const held = h && p && h.held_usd != null && p.held_usd != null ? h.held_usd + p.held_usd : null;
      const counted = counts && ['pass','fail','ungraded'].every(key => Number.isInteger(counts[key]));
      return '<tr><td>' + esc(row.provider) + '</td><td>' + esc(counted ? [counts.pass, counts.fail, counts.ungraded].join(' / ') : row.state === 'not-run' ? 'not run' : 'ungraded') +
        '</td><td>' + esc(row.retention_max_graded ? row.retention_points + ' / ' + row.retention_max_graded : 'ungraded') +
        '</td><td>' + money(h?.settled_upper_bound_usd) + '</td><td>' + money(p?.settled_upper_bound_usd) +
        '</td><td>' + money(held) + '</td><td>' + esc(h && p ? h.model_requests + p.model_requests : 'unknown') +
        '</td><td>' + (row.complete_for_cost_comparison ? 'yes' : 'no') + '</td><td>' +
        esc(row.state === 'not-run' ? 'not run' : row.deferred_processing?.pending_items != null ? row.deferred_processing.pending_items + ' pending' : 'unobserved') + '</td></tr>';
    }).join('') + '</tbody></table></div>' +
    review.providers.map(row => details(row.provider + ' · phases, tokens and deferred work', dump(row))).join('') +
    (review.other_attempts?.length ? details('Previous and interrupted attempts · separate costs',dump(review.other_attempts)) : '') +
    details('Cost scope and limitations', dump(review.scope)) + '</section>';
}
function technicalPage(run) {
  return '<section class="stage"><h2>Saved run files</h2><p class="muted">These records are here for debugging. The Timeline shows texts and provider activity by simulated day. Authoring and Judges show how the test was made and evaluated.</p>' +
    details("Original and resolved author spec", dump({author: run.spec, resolved: run.resolved_spec})) +
    details("Frozen bundle and contract", dump({bundle: run.bundle, contract: run.contract})) +
    details("Model requests and responses · " + (run.model_calls || []).length,
      (run.model_calls || []).map(call => details(call.record?.model + " · " + call.file.split('/').pop().slice(0, 12),
        renderModelCall(call.record))).join("")) +
    details("Allowed and blocked recall tools", dump(Object.fromEntries(Object.entries(run.arms || {}).map(([key,arm]) => [key,arm.tool_policy])))) +
    (run.development_review ? details('Development review',dump(run.development_review)) : '') +
    (run.failure_review ? details('Failure review (original grades unchanged)',dump(run.failure_review)) : '') +
    details("Experiment status and run audit", dump({status: run.experiment_status,
      summary: run.summary, audit: run.audit})) + "</section>";
}
function render() {
  const run = selected();
  if (!run) { el("content").innerHTML = notice("No saved run selected."); return; }
  const items = [["timeline", "Day by day"], ["story", "Authoring"], ["judge", "Criteria & judges"],
    ...(run.cost_review ? [["costs","Costs & work"]] : []),
    ...(run.preflight ? [["preflight","Native canary"]] : []), ["details", "Saved files"]];
  if (run.arms?.[page]) { selectedProvider = page; page = "timeline"; }
  if (!items.some(item => item[0] === page)) page = "timeline";
  const status = stateOf(run);
  const alert = run.audit?.state === "fail" ?
    '<div class="alert bad"><strong>Run audit failed.</strong> Results need review.' + details('Audit findings', dump(run.audit.errors)) + '</div>' :
    run.audit?.valid_for_comparison === false ?
    '<div class="alert bad">Excluded: ' + esc(run.audit.finding) + "</div>" :
    run.summary?.error ? '<div class="alert bad">Stopped during ' +
      esc(run.summary.failed_stage || "experiment") + ": " + esc(run.summary.error) + "</div>" : "";
  const body = page === "preflight" ? '<section class="stage"><h2>Native canary</h2><p>A separate small corpus checks ingestion and recall before the month. These answers are not month scores.</p></section>' + dayTimeline(run.preflight) :
    page === "timeline" ? dayTimeline(run) : page === "story" ? semanticStory(run) :
    page === "judge" ? judgePage(run) : page === "costs" ? costPage(run) : page === "details" ? technicalPage(run) : dayTimeline(run);
  el("content").innerHTML = '<div class="run-heading"><div><span class="eyebrow">' +
    "Experiment" + '</span><h1>' +
    esc(titleOf(run)) + '</h1><p>' + esc(idOf(run)) +
    '</p></div>' + (run.audit?.state === "pass" ? badge("Trace audited", "ok") : "") +
    badge(status, status === "complete" ? "ok" :
      status === "excluded" || status === "incomplete" ? "bad" : "neutral") +
    "</div>" + alert + nav(items) + '<div class="page-body">' + body + "</div>";
}
async function refresh() {
  el("refresh").disabled = true;
  try {
    const response = await fetch("/runs?at=" + Date.now(), {cache:"no-store"});
    if (!response.ok) throw Error("HTTP " + response.status);
    data = await response.json();
    const params = new URLSearchParams(location.search);
    const requested = params.get("run") || current;
    const run = data.runs.find(item => idOf(item) === requested) || data.runs.find(item => Object.values(item.arms || {}).some(arm => arm.exchanges?.length)) || data.runs[0];
    el("data-time").textContent = data.generated_at ? "Updated " + data.generated_at : "";
    if (run) {
      current = idOf(run);
      selectedDay = params.get("day") || selectedDay;
      selectedProvider = params.get("provider") || selectedProvider;
      choose(current, params.get("view") || page);
    }
    else {
      current = "";
      renderRunPicker();
      el("content").innerHTML = notice("No saved runs found.");
    }
    if (data.errors?.length) el("content").insertAdjacentHTML("afterbegin",
      '<div class="alert bad">Could not read ' + esc(data.errors.map(item => item.run + ": " + item.error).join("; ")) + '</div>');
  } catch (error) {
    el("content").innerHTML = '<div class="alert bad">Could not load runs: ' +
      esc(error.message) + "</div>";
  } finally {
    el("refresh").disabled = false;
  }
}
document.addEventListener("click", event => {
  const target = event.target;
  const pageButton = target.closest("[data-page]");
  if (pageButton) { page = pageButton.dataset.page; choose(current, page); return; }
  const dayButton = target.closest("[data-day]");
  if (dayButton) { selectedDay = dayButton.dataset.day; choose(current, "timeline", false); document.querySelector(".day-navigation")?.scrollIntoView({block:"start"}); return; }
  const providerButton = target.closest("[data-provider]");
  if (providerButton) { selectedProvider = providerButton.dataset.provider; choose(current, "timeline", false); return; }
  const sourceButton = target.closest("[data-jump]");
  if (sourceButton) {
    const sourceMessage = message(sourceButton.dataset.jump);
    selectedDay = dayKey(sourceMessage?.written_at, selected());
    choose(current, "timeline");
    const source = [...document.querySelectorAll("[data-message]")].find(
      node => node.dataset.message === sourceButton.dataset.jump);
    if (source) {
      source.scrollIntoView({block: "center", behavior: "smooth"});
      source.classList.remove("flash");
      void source.offsetWidth;
      source.classList.add("flash");
    }
    return;
  }
  const emailButton = target.closest("[data-email]");
  if (emailButton) {
    const item = message(emailButton.dataset.email);
    const app = emailButton.closest(".mail-app");
    app.querySelectorAll(".mail-item").forEach(node => node.classList.remove("active"));
    emailButton.classList.add("active");
    app.querySelector(".mail-read").innerHTML = emailRead(item);
  }
});
el("run-select").addEventListener("change", event => choose(event.target.value));
el("refresh").addEventListener("click", refresh);
refresh();
