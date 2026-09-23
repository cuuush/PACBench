"use strict";

let data = {runs: []};
let current = "";
let page = "story";
const el = id => document.getElementById(id);
const esc = value => String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
const dump = value => "<pre>" + esc(JSON.stringify(value, null, 2)) + "</pre>";
const short = value => value ? esc(String(value).slice(0, 12)) : "not captured";
const notice = text => '<div class="notice">' + esc(text) + "</div>";
const details = (title, body, extra = "") => '<details class="fold ' + extra + '"><summary>' + esc(title) + '</summary><div class="fold-body">' + body + "</div></details>";
const badge = (text, tone = "") => '<span class="badge ' + tone + '">' + esc(text) + "</span>";
const idOf = run => run.id || run.dir;
const sources = run => run.kind === "semantic" ? (run.observations || []) : ((run.exposure || {}).exposed || []);
const sourceLink = id => {
  const match = String(id).match(/-(\d+)$/);
  const label = match ? "Message " + (Number(match[1]) + 1) : "Show message";
  return '<button class="source-link" type="button" data-jump="' + esc(id) + '">' + label + '</button>';
};

function stateOf(run) {
  if (run.audit?.valid_for_comparison === false) return "excluded";
  if (run.kind === "historical") return "historical";
  if (run.summary?.state) return run.summary.state;
  if (run.experiment_status?.state && run.experiment_status.state !== "running")
    return run.experiment_status.state;
  const arms = Object.values(run.arms || {});
  return arms.length && arms.every(arm => arm.status?.state === "valid") ? "complete" : "incomplete";
}
function titleOf(run) {
  return run.spec?.id || run.life || idOf(run);
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
function choose(id, nextPage = "story") {
  if (!data.runs.some(run => idOf(run) === id)) return;
  current = id;
  page = nextPage;
  renderRunPicker();
  render();
  history.replaceState(null, "", "?run=" + encodeURIComponent(id) +
    (page === "story" ? "" : "&view=" + encodeURIComponent(page)));
  window.scrollTo(0, 0);
}
function nav(items) {
  if (!items.some(item => item[0] === page)) page = "story";
  return '<nav class="pages" aria-label="Run pages">' + items.map(item =>
    '<button type="button" data-page="' + esc(item[0]) + '" class="' +
    (page === item[0] ? "active" : "") + '">' + esc(item[1]) + "</button>").join("") + "</nav>";
}
function observation(id) {
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
  const heading = names.length ? names.join(", ") : "Conversation";
  let priorDay = "";
  let rows = "";
  for (const item of items) {
    const nextDay = day(item.written_at);
    if (nextDay !== priorDay) rows += '<div class="chat-day">' + nextDay + "</div>";
    const owner = item.from_owner === true;
    rows += '<div class="chat-row ' + (owner ? "mine" : "theirs") +
      '" data-observation="' + esc(item.source_id) + '">' +
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
  const to = item.from_owner ? item.thread : "You";
  const subject = String(item.text || "").split("\n")[0].slice(0, 90) || "(no subject)";
  return '<div data-observation="' + esc(item.source_id) + '"><h3>' + esc(subject) +
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
  const name = String(path || "").split("/").pop();
  return (run.model_calls || []).find(call => call.file === name)?.record;
}
function modelReasoning(call) {
  const message = call?.response?.choices?.[0]?.message;
  return message?.reasoning_content || message?.reasoning ||
    call?.response?.choices?.[0]?.reasoning || null;
}
function renderModelCall(call) {
  if (!call) return notice("Model call record not captured.");
  const reasoning = modelReasoning(call);
  return '<div class="model-meta">' + esc(call.model || "model not captured") +
    " · " + esc(call.created_at || "time not captured") + " · " +
    esc(call.usage?.total_tokens ?? "tokens not reported") + ' tokens</div>' +
    '<h4>Prompt sent</h4>' + dump(call.request?.messages || []) +
    '<h4>Output returned</h4><pre>' + esc(call.text || "") + "</pre>" +
    '<p class="muted">' + (reasoning ? "Model reasoning returned:" : "Private model reasoning was not returned by this API call.") +
    "</p>" + (reasoning ? '<pre>' + esc(reasoning) + "</pre>" : "") +
    details("Full model request and response", dump(call));
}
function reviewedLines(run, sceneId, lineNumbers) {
  const prefix = "/" + sceneId + "-";
  const items = sources(run).filter(item => item.source_id.includes(prefix));
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
      (factRows || '<p class="muted">Scenario evidence: ' +
        reviewedLines(run, scene.scene_id, verdict.evidence) + "</p>") +
      details("Reviewer prompt and full decision", '<h4>Prompt</h4><pre>' +
        esc(record.judge_prompt || "not captured") + '</pre><h4>Response</h4><pre>' +
        esc(record.judge_response || "not captured") + "</pre>" +
        details("Full review record", dump(record))) + "</div>";
  }).join("");
}
function renderContract(run) {
  if (!run.contract) return notice("Claim contract was not frozen.");
  return '<div class="contract-question"><span class="eyebrow">Question for both providers</span><p>' +
    esc(run.contract.question) + "</p></div>" +
    (run.contract.claims || []).map(claim => '<div class="contract-fact"><span>' +
      esc(claim.statement) + '</span><span>' +
      (claim.evidence || []).map(sourceLink).join(" ") + "</span></div>").join("") +
    details("Contract review and hashes", dump(run.contract));
}
function semanticStory(run) {
  const plans = run.spec?.scenes || [];
  const made = run.scenes?.scenes || [];
  const instructions = plans.map((plan, index) =>
    '<div class="scene-intent"><span class="eyebrow">Scene ' + (index + 1) +
      '</span><p>' + esc(plan.scene) + "</p>" +
      (plan.facts?.length ? '<div class="requested-facts"><strong>Facts that must appear</strong>' +
        plan.facts.map(fact => "<div>• " + esc(fact) + "</div>").join("") + "</div>" : "") +
      (plan.test_intent ? '<p class="muted">Recall intent: ' + esc(plan.test_intent) + "</p>" : "") +
    "</div>").join("");
  const renderAudit = made.map((scene, index) => {
    const paths = scene.record?.generation_paths || [];
    const note = (scene.record?.writer_notes || []).filter(Boolean).at(-1);
    return '<section class="stage"><h2>Rendering · scene ' + (index + 1) + '</h2>' +
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
    '<section class="stage"><h2>Facts ready for recall</h2><p class="muted">These claims come from the required facts and cited dialogue. They were frozen before either assistant answered.</p>' +
    renderContract(run) + "</section>";
}
function historicalStory(run) {
  return '<section class="stage"><h2>Saved conversation</h2>' +
    (run.bundle_match === true ? "" :
      notice("The original full bundle was not captured. These messages are the run's saved exposure; today's fixture was not substituted.")) +
    conversations(run) + details("Saved exposure and hash", dump({
      bundle_hash: run.bundle_hash, exposure: run.exposure
    })) + "</section>";
}
function parseResult(result) {
  let value = result?.text;
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
  const parsed = parseResult(result);
  for (const hit of parsed?.results || []) {
    const id = arm?.archive_sources?.[String(hit.line_id)];
    if (id && !ids.includes(id)) ids.push(id);
  }
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
  if (!calls.length) return '<div class="quiet">No tool calls recorded for this answer.</div>';
  const memory = [];
  const other = [];
  calls.forEach((call, index) => {
    const result = turn.tool_results?.[index];
    const blocked = result?.policy_blocked || String(result?.text || "").includes("PACBENCH-BLOCKED");
    const query = call.input?.query || call.input?.line_id || "";
    const card = '<details class="tool-card"><summary><span class="tool-icon">⌕</span><span><strong>' +
      esc(toolName(call.name)) + '</strong><small>' +
      esc(query || (blocked ? "Blocked" : result?.success === false ? "Failed" : "Open output")) +
      '</small></span><span class="tool-state">' + (blocked ? "blocked" : result?.success === true ? "returned" : "not captured") +
      '</span></summary><div class="tool-body"><h4>Arguments</h4>' + dump(call.input || {}) +
      '<h4>What came back</h4>' + resultBody(run, arm, result) + "</div></details>";
    (call.name?.startsWith("memcal_") || call.name?.startsWith("mcp__hindsight__") ?
      memory : other).push(card);
  });
  return memory.join("") + (other.length ?
    details("Tool setup and other calls · " + other.length, other.join("")) : "");
}
function score(ex) {
  const grade = ex.grade;
  if (!grade) return ["Ungraded", "neutral"];
  if (grade.misrepresented) return [grade.misrepresented + " wrong detail" +
    (grade.misrepresented === 1 ? "" : "s"), "bad"];
  if (grade.missed) return [grade.missed + " missed detail" +
    (grade.missed === 1 ? "" : "s"), "warn"];
  return ["All facts correct", "ok"];
}
function scoreDetails(run, ex) {
  const [label, tone] = score(ex);
  const rows = (ex.grade?.claims || []).map(item => {
    const claim = run.contract?.claims?.find(c => c.id === item.id);
    return '<div class="verdict-row">' + badge(item.verdict, item.verdict === "correct" ? "ok" :
      item.verdict === "missed" ? "warn" : "bad") + '<div><strong>' +
      esc(claim?.statement || item.id) + '</strong><p>' + esc(item.reason) + '</p>' +
      (claim?.evidence || []).map(sourceLink).join(" ") + "</div></div>";
  }).join("");
  return '<details class="answer-score ' + tone + '"><summary>' + badge(label, tone) +
    '<span>See why</span></summary><div class="fold-body">' +
    (rows || notice("A complete answer judgment was not captured. This answer is ungraded.")) +
    (ex.judgment ? details("Answer judge prompt and response", '<h4>Prompt</h4><pre>' +
      esc(ex.judgment.prompt || "not captured") + '</pre><h4>Response</h4><pre>' +
      esc(ex.judgment.response || "not captured") + "</pre>") : "") + "</div></details>";
}
function questionText(turn, ex) {
  return ex.judgment?.question ||
    String(turn.prompt || "").replace(/^\[Simulated now:[^\n]+\]\n\n/, "");
}
function providerExchange(run, arm, ex, index) {
  const turn = ex.turn;
  if (!turn) return notice("Hermes turn " + (index + 1) + " not captured.");
  return '<section class="exchange"><div class="exchange-label">Question ' + (index + 1) +
    " · " + esc(turn.at || "time not captured") + '</div>' +
    '<div class="chat-row mine"><div class="chat-message"><div class="bubble">' +
      esc(questionText(turn, ex)) + "</div></div></div>" +
    '<div class="tool-sequence">' + toolCards(run, arm, turn) + "</div>" +
    '<div class="chat-row theirs"><span class="avatar hermes-avatar">H</span><div class="chat-message">' +
      '<div class="sender">Hermes</div><div class="bubble answer-bubble">' +
      esc(turn.answer || "Answer not captured.") + "</div>" +
      scoreDetails(run, ex) + "</div></div></section>";
}
function providerPage(run, provider) {
  const arm = run.arms?.[provider];
  if (!arm) return notice("This provider did not run.");
  const status = arm.status || {};
  const access = status.capability?.available;
  const used = status.behavior?.retrieval_called;
  const exchanges = arm.exchanges || (arm.turn ? [{turn: arm.turn, judgment: arm.judgment, grade: arm.grade}] : []);
  const brief = provider === "memcal" ?
    '<section class="brief"><strong>Memory brief visible to Hermes</strong>' +
      (arm.turn?.prefetch_context != null ? '<pre>' + esc(arm.turn.prefetch_context) + "</pre>" :
        notice("Brief not captured.")) + "</section>" :
    '<p class="muted">Hindsight provided no automatic brief. Recall results appear between the question and answer below.</p>';
  return '<div class="provider-head"><h2>' + esc(provider === "memcal" ? "Memcal" : "Hindsight") +
    '</h2><div class="provider-badges">' +
    badge(access === true ? "Memory access verified" : access === false ? "Memory unavailable" : "Access not captured",
      access === true ? "ok" : access === false ? "bad" : "neutral") +
    badge(used === true ? "Hermes used memory" : used === false ? "Hermes did not retrieve" : "Use not captured",
      used === true ? "ok" : used === false ? "warn" : "neutral") + "</div></div>" +
    (status.reason ? notice(status.reason) : "") +
    (run.audit?.valid_for_comparison === false ? notice("Excluded from comparison: " + run.audit.finding) : "") +
    brief + (exchanges.length ? exchanges.map((ex, i) => providerExchange(run, arm, ex, i)).join("") :
      notice("No assistant question or answer was saved for this provider.")) +
    '<p class="muted calibration">Answer badges are diagnostic until the proposed calibration labels are human reviewed.</p>' +
    details("Provider setup and saved files", '<h4>Ingestion</h4>' + dump(arm.ingestion) +
      '<h4>Maintenance and readiness</h4>' + dump(arm.maintenance) +
      '<h4>Tool discovery and preflight</h4>' + dump(arm.preflight) +
      '<h4>Provider status</h4>' + dump(arm.status));
}
function historicalProvider(run) {
  const turns = run.turns || [];
  let priorSnapshot = null;
  return '<div class="provider-head"><h2>' + esc(run.provider || "Provider") +
    '</h2>' + badge("Historical run", "neutral") + '</div>' +
    notice("Saved Hermes session context is shown for each turn when available. Provider internal state beyond saved records is not captured.") +
    (turns.length ? turns.map((turn, index) => {
      const context = turn.prefetch_context;
      const snapshot = context?.match(/MEMCAL SNAPSHOT[^\n]*\[id=([^\]]+)\]/)?.[1] || null;
      const contextLabel = !context ? (run.provider === "hindsight" ?
        "No automatic brief; recall appears in tool calls" : "Memory context not captured") :
        snapshot && snapshot === priorSnapshot ? "Memcal brief sent again · unchanged snapshot" :
        "Memcal brief sent · refreshed snapshot";
      const earlyEvidence = (run.audit?.temporal_exposure || []).filter(row => row.turn === index + 1);
      if (snapshot) priorSnapshot = snapshot;
      return '<section class="exchange"><div class="exchange-label">Question ' + (index + 1) +
      " · " + esc(turn.at || "time not captured") + '</div>' +
      (earlyEvidence.length ? notice("Timing flaw: " + earlyEvidence.length +
        " later evidence batch(es) were ingested before this turn. Open Saved files for source IDs.") : "") +
      (context ? details(contextLabel, '<pre>' + esc(context) + "</pre>") : notice(contextLabel)) +
      '<div class="chat-row mine"><div class="chat-message"><div class="bubble">' +
      esc(turn.prompt || "Question not captured") + "</div></div></div>" +
      '<div class="tool-sequence">' + toolCards(run, {}, turn) + "</div>" +
      '<div class="chat-row theirs"><span class="avatar hermes-avatar">H</span><div class="chat-message">' +
      '<div class="sender">Hermes</div><div class="bubble answer-bubble">' +
      esc(turn.answer || "Answer not captured") + "</div>" +
      details("Legacy grade · semantic verdict not captured", dump(run.grades?.[index])) +
      "</div></div></section>";
    }).join("") : notice("No Hermes turns captured."));
}
function technicalPage(run) {
  if (run.kind === "historical") return '<section class="stage"><h2>Saved run files</h2>' +
    details("Timing audit", dump(run.audit || {finding: "No recorded timing violation; other internal state may be absent."})) +
    details("Evidence and provenance", dump({bundle_hash: run.bundle_hash,
      exposure: run.exposure, import_batches: run.import_batches,
      maintenance: run.maintenance, session_schedule: run.session_schedule})) + "</section>";
  return '<section class="stage"><h2>Saved run files</h2><p class="muted">These records are here for debugging. The Story and provider pages show the readable path through the experiment.</p>' +
    details("Original and resolved author spec", dump({author: run.spec, resolved: run.resolved_spec})) +
    details("Frozen bundle and contract", dump({bundle: run.bundle, contract: run.contract})) +
    details("Model requests and responses · " + (run.model_calls || []).length,
      (run.model_calls || []).map(call => details(call.record?.model + " · " + call.file.slice(0, 12),
        renderModelCall(call.record))).join("")) +
    details("Experiment status and comparison audit", dump({status: run.experiment_status,
      summary: run.summary, audit: run.audit})) + "</section>";
}
function render() {
  const run = selected();
  if (!run) { el("content").innerHTML = notice("No saved run selected."); return; }
  const historic = run.kind === "historical";
  const items = historic ?
    [["story", "Story"], ["provider", "Assistant"], ["details", "Saved files"]] :
    [["story", "Story"], ...Object.keys(run.arms || {}).map(name => [name, name === "memcal" ? "Memcal" : "Hindsight"]),
      ["details", "Saved files"]];
  if (!items.some(item => item[0] === page)) page = "story";
  const status = stateOf(run);
  const alert = run.audit?.valid_for_comparison === false ?
    '<div class="alert bad">Excluded: ' + esc(run.audit.finding) + "</div>" :
    run.summary?.error ? '<div class="alert bad">Stopped during ' +
      esc(run.summary.failed_stage || "experiment") + ": " + esc(run.summary.error) + "</div>" : "";
  const body = page === "story" ? (historic ? historicalStory(run) : semanticStory(run)) :
    page === "details" ? technicalPage(run) :
      historic ? historicalProvider(run) : providerPage(run, page);
  el("content").innerHTML = '<div class="run-heading"><div><span class="eyebrow">' +
    (historic ? "Historical evidence" : "Experiment") + '</span><h1>' +
    esc(titleOf(run)) + '</h1><p>' + esc(idOf(run)) +
    '</p></div>' + badge(status, status === "complete" ? "ok" :
      status === "excluded" || status === "incomplete" ? "bad" : "neutral") +
    "</div>" + alert + nav(items) + '<div class="page-body">' + body + "</div>";
}
async function refresh() {
  el("refresh").disabled = true;
  try {
    const response = await fetch("data/dashboard.json?at=" + Date.now());
    if (!response.ok) throw Error("HTTP " + response.status);
    data = await response.json();
    const params = new URLSearchParams(location.search);
    const requested = params.get("run") || current;
    const run = data.runs.find(item => idOf(item) === requested) || data.runs[0];
    el("data-time").textContent = data.generated_at ? "Updated " + data.generated_at : "";
    if (run) choose(idOf(run), params.get("view") || page);
    else el("content").innerHTML = notice("No saved runs found.");
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
  const sourceButton = target.closest("[data-jump]");
  if (sourceButton) {
    if (page !== "story") choose(current, "story");
    const source = [...document.querySelectorAll("[data-observation]")].find(
      node => node.dataset.observation === sourceButton.dataset.jump);
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
    const item = observation(emailButton.dataset.email);
    const app = emailButton.closest(".mail-app");
    app.querySelectorAll(".mail-item").forEach(node => node.classList.remove("active"));
    emailButton.classList.add("active");
    app.querySelector(".mail-read").innerHTML = emailRead(item);
  }
});
el("run-select").addEventListener("change", event => choose(event.target.value));
el("refresh").addEventListener("click", refresh);
refresh();
