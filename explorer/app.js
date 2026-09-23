"use strict";
/* PACBench Explorer: corpus browser + run-results viewer. Local data/ only. */
var CORPUS = {}, RUNS = [], PENDING = [], META = {};
var CHAT = ["imessage", "whatsapp", "text", "slack", "groupme", "hermes"];
var GROUPCH = ["whatsapp", "slack", "groupme"];
var S = { life: null, day: null, chan: "all", thr: "all", q: "" };
var RUNSEL = null;

function esc(s) {
  return String(s == null ? "" : s).replace(/&/g, "&amp;")
    .replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}
function hue(s) {
  var h = 0, i; s = String(s);
  for (i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360;
  return h;
}
function subjOf(t) {
  var l = String(t == null ? "" : t).split("\n")[0];
  var c = l.split(/[.!?;:]/)[0] || l;
  return c.trim().slice(0, 90) || "(no subject)";
}

/* ---------- tabs ---------- */
function showTab(which) {
  document.getElementById("corpus").hidden = which !== "corpus";
  document.getElementById("runs").hidden = which !== "runs";
  document.getElementById("tab-corpus").classList.toggle("active", which === "corpus");
  document.getElementById("tab-runs").classList.toggle("active", which === "runs");
}

/* ---------- corpus browser (ported from docs/corpus-explorer.html) ---------- */
function obs() { return CORPUS[S.life].obs; }
function days() {
  var m = {}, r = [];
  obs().forEach(function (o) {
    var d = (o.collected_at || "").slice(0, 10); m[d] = (m[d] || 0) + 1;
  });
  Object.keys(m).sort().forEach(function (d) { r.push([d, m[d]]); });
  return r;
}
function meta(o) {
  return "source_id: " + esc(o.source_id) + "<br>written_at: " + esc(o.written_at) +
    "<br>collected_at: " + esc(o.collected_at);
}
function chatHTML(t, msgs) {
  var ch = msgs[0].channel, se = {}, i;
  for (i = 0; i < msgs.length; i++) if (!msgs[i].from_owner) se[msgs[i].sender] = 1;
  var grp = GROUPCH.indexOf(ch) >= 0 || Object.keys(se).length > 1;
  var h = '<div class="conv ' + (grp ? "grp" : "dm") + '"><h3>' + esc(t) +
    " &middot; " + esc(ch) + " (" + msgs.length + ")</h3><div class='cwin'>";
  var prev = null, pday = null;
  msgs.forEach(function (o) {
    var day = (o.written_at || "").slice(0, 10), tm = (o.written_at || "").slice(11, 16);
    if (day !== pday) {
      h += '<div class="daydiv"><span>' + esc(day) + "</span></div>"; prev = null; pday = day;
    }
    var jn = (prev === o.sender);
    var sid = ' data-sid="' + esc(o.source_id) + '"';
    if (o.from_owner) {
      h += '<div class="row me' + (jn ? " joined" : "") + '"' + sid + '"><div class="col"><div class="bub">' +
        esc(o.text) + '<div class="tm">' + esc(tm) + '</div><details><summary>provenance</summary>' +
        meta(o) + "</details></div></div></div>";
    } else {
      var hh = hue(String(o.sender)), init = esc(String(o.sender || "?").slice(0, 1).toUpperCase());
      var av = jn ? '<div class="av ghost"></div>' :
        '<div class="av" style="background:hsl(' + hh + ',55%,45%)">' + init + "</div>";
      var nm = (grp && !jn) ? '<div class="nm">' + esc(o.sender) + "</div>" : "";
      h += '<div class="row' + (jn ? " joined" : "") + '"' + sid + ">" + av + '<div class="col">' + nm +
        '<div class="bub">' + esc(o.text) + '<div class="tm">' + esc(tm) +
        '</div><details><summary>provenance</summary>' + meta(o) + "</details></div></div></div>";
    }
    prev = o.sender;
  });
  return h + "</div></div>";
}
function readHTML(o) {
  var from = o.from_owner ? "me" : o.sender, to = o.from_owner ? o.thread : "me";
  return '<div class="subj">' + esc(subjOf(o.text)) + "</div><div class='mh'><div><b>From:</b> " +
    esc(from) + "</div><div><b>To:</b> " + esc(to) + "</div><div><b>Date:</b> " + esc(o.written_at) +
    "</div><div><b>Thread:</b> " + esc(o.thread) + "</div></div><div class='body'>" + esc(o.text) +
    "</div><details><summary>provenance &middot; " + esc(o.source_id) + "</summary>" + meta(o) + "</details>";
}
function mailHTML(t, msgs) {
  var h = '<div class="conv"><h3>' + esc(t) + " &middot; email (" + msgs.length +
    ")</h3><div class='mailapp'><div class='maillist'>";
  msgs.forEach(function (o, i) {
    h += '<div class="mailitem' + (i === 0 ? " active" : "") + '" data-sid="' + esc(o.source_id) +
      '" data-mid="' + esc(o.source_id) + '"><div class="mi-from">' + esc(o.from_owner ? "me" : o.sender) +
      '</div><div class="mi-subj">' + esc(subjOf(o.text)) + '</div><div class="mi-date">' +
      esc((o.written_at || "").slice(0, 16).replace("T", " ")) + "</div></div>";
  });
  return h + "</div><div class='mailread' data-sid=\"" + esc(msgs[0].source_id) + "\">" +
    readHTML(msgs[0]) + "</div></div></div>";
}
function byId(sid) {
  var r = null;
  Object.keys(CORPUS).forEach(function (lid) {
    CORPUS[lid].obs.forEach(function (o) { if (o.source_id === sid) r = { life: lid, o: o }; });
  });
  return r;
}
function renderCorpus() {
  var L = CORPUS[S.life];
  document.getElementById("h-day").textContent =
    "life: " + S.life + " · bundle " + L.hash.slice(0, 12) + " (" + L.bundle + ") · day " + S.day;
  var dl = document.getElementById("day"), ds = days(), cur = dl.value;
  dl.innerHTML = ds.map(function (d) {
    return '<option value="' + d[0] + '">' + d[0] + " (" + d[1] + ")</option>";
  }).join("");
  if (ds.some(function (d) { return d[0] === cur; })) dl.value = cur;
  else if (!ds.some(function (d) { return d[0] === S.day; })) { S.day = ds[0][0]; dl.value = S.day; }
  else dl.value = S.day;
  var ch = document.getElementById("chan");
  if (!ch.options.length) {
    var cs = ["all"].concat(CHAT.concat(["email"]));
    ch.innerHTML = cs.map(function (c) { return "<option>" + c + "</option>"; }).join("");
  }
  ch.value = S.chan;
  var th = document.getElementById("thr"), threads = {};
  obs().forEach(function (o) { threads[o.thread] = 1; });
  var tl = ["all"].concat(Object.keys(threads).sort());
  th.innerHTML = tl.map(function (t) {
    return '<option value="' + esc(t) + '">' + esc(t) + "</option>";
  }).join("");
  if (tl.indexOf(S.thr) < 0) S.thr = "all";
  th.value = S.thr;
  var q = S.q.toLowerCase();
  var list = obs().filter(function (o) {
    return (o.collected_at || "").slice(0, 10) === S.day &&
      (S.chan === "all" || o.channel === S.chan) &&
      (S.thr === "all" || o.thread === S.thr) &&
      (!q || (o.text || "").toLowerCase().indexOf(q) >= 0 ||
        (o.sender || "").toLowerCase().indexOf(q) >= 0);
  });
  list.sort(function (a, b) {
    return (a.written_at < b.written_at ? -1 : a.written_at > b.written_at ? 1 : 0) ||
      (a.source_id < b.source_id ? -1 : 1);
  });
  document.getElementById("count").textContent = list.length + " shown";
  var v = document.getElementById("view"), h = "";
  var chat = list.filter(function (o) { return CHAT.indexOf(o.channel) >= 0; });
  var mail = list.filter(function (o) { return o.channel === "email"; });
  var other = list.filter(function (o) { return CHAT.indexOf(o.channel) < 0 && o.channel !== "email"; });
  var byT = {};
  chat.forEach(function (o) { (byT[o.thread] = byT[o.thread] || []).push(o); });
  Object.keys(byT).sort().forEach(function (t) { h += chatHTML(t, byT[t]); });
  var byM = {};
  mail.forEach(function (o) { (byM[o.thread] = byM[o.thread] || []).push(o); });
  Object.keys(byM).sort().forEach(function (t) { h += mailHTML(t, byM[t]); });
  other.forEach(function (o) {
    h += '<div class="mail" data-sid="' + esc(o.source_id) + '"><div class="mi-from">[' +
      esc(o.channel) + "] " + esc(o.sender) + '</div><div>' + esc(o.text) +
      '</div><div class="mi-date">' + esc(o.written_at) +
      '</div><details><summary>' + esc(o.source_id) + "</summary>" + meta(o) + "</details></div>";
  });
  v.innerHTML = h || "<p>No observations match.</p>";
  Array.prototype.forEach.call(v.querySelectorAll(".mailitem"), function (el) {
    el.onclick = function () {
      var box = el.parentNode.parentNode;
      Array.prototype.forEach.call(box.querySelectorAll(".mailitem"), function (x) {
        x.classList.remove("active");
      });
      el.classList.add("active");
      var found = byId(el.getAttribute("data-mid"));
      if (found) box.querySelector(".mailread").innerHTML = readHTML(found.o);
    };
  });
}
/* Jump from a runs-view evidence link to the message in the corpus view. */
function jumpToEvidence(sid) {
  var selected = RUNS.filter(function (r) { return r.dir === RUNSEL; })[0];
  var life = selected && selected.evidence_life;
  var own = life && CORPUS[life] && CORPUS[life].obs.filter(function (o) { return o.source_id === sid; })[0];
  var found = own ? { life: life, o: own } : byId(sid);
  if (!found) return;
  showTab("corpus");
  S.life = found.life;
  document.getElementById("life").value = S.life;
  S.day = (found.o.collected_at || "").slice(0, 10);
  S.chan = "all"; S.thr = found.o.thread; S.q = "";
  document.getElementById("q").value = "";
  renderCorpus();
  var el = document.querySelector('[data-sid="' + CSS.escape(sid) + '"]');
  if (el) {
    el.scrollIntoView({ block: "center" });
    el.classList.remove("flash");
    void el.offsetWidth;
    el.classList.add("flash");
  }
}

/* ---------- runs viewer ---------- */
function pill(v) {
  v = String(v || "").toLowerCase();
  if (v === "correct") return '<span class="pill ok">CORRECT</span>';
  if (v === "missed") return '<span class="pill miss">MISSED</span>';
  if (v === "misrepresented") return '<span class="pill mis">MISREPRESENTED</span>';
  return '<span class="pill na">' + esc(v || "?") + "</span>";
}
function fmtNum(x) {
  return (x == null || x !== x) ? "—" : (Math.round(x * 1000) / 1000);
}
function slotRows(g) {
  var crit = g.criteria || {};
  var verdictById = {};
  (g.slots || []).forEach(function (s) {
    verdictById[s.id] = { verdict: s.verdict, reason: s.reason };
  });
  function oneRow(s, isOpt) {
    var v = verdictById[s.id] || {};
    var bad = v.verdict === "misrepresented", warn = v.verdict === "missed";
    var ev = (s.supported_by || []).map(function (sid) {
      return '<span class="evlink" data-ev="' + esc(sid) + '">' + esc(sid) + "</span>";
    }).join("<br>");
    return '<tr class="' + (bad ? "slot-bad" : warn ? "slot-warn" : "") + '"><td>' + esc(s.id) +
      (isOpt ? " (option)" : "") + "</td><td>" + esc(s.kind) + " / " + esc(s.norm) + "</td>" +
      "<td><b>" + esc(s.expected) + "</b>" +
      (s.alternatives && s.alternatives.length ? "<br>alt: " + esc(s.alternatives.join(", ")) : "") +
      (s.forbidden && s.forbidden.length ? "<br>forbidden: " + esc(s.forbidden.join(", ")) : "") +
      (s.error ? "<br>error class: " + esc(s.error) : "") + "</td>" +
      "<td>" + pill(v.verdict) + (v.reason ? "<br>" + esc(v.reason) : "") + "</td>" +
      "<td>" + (ev || "—") + "</td></tr>";
  }
  var rows = (crit.slots || []).map(function (s) { return oneRow(s, false); });
  (crit.supported || []).forEach(function (s) { rows.push(oneRow(s, true)); });
  (crit.forbidden_options || []).forEach(function (s) { rows.push(oneRow(s, true)); });
  if (!rows.length) return "<p>No slot criteria recorded.</p>";
  return '<table class="slots"><tr><th>slot</th><th>kind / norm</th>' +
    "<th>expected (alternatives, forbidden)</th><th>verdict</th><th>evidence</th></tr>" +
    rows.join("") + "</table>";
}
function runTimeline(r) {
  var events = [], exposure = r.exposure || {};
  (r.import_batches || []).forEach(function (b) {
    events.push({ at: b.collected_at || b.batch_day, order: 0,
      label: "ingest · " + (b.observations || []).length + " observations",
      body: '<div class="crit">' + esc((b.usage || {}).notes || []) +
        '</div><div class="crit">ingested at ' + esc(b.ingested_at || "unknown") +
        ' · exposure digest ' + esc(b.exposure_digest || "unknown") + '</div>' +
        '<div class="trace">' + (b.observations || []).map(function (id) {
          return '<span class="evlink" data-ev="' + esc(id) + '">' + esc(id) + '</span>';
        }).join(" · ") + '</div>' });
  });
  ((r.maintenance || {}).events || []).forEach(function (m) {
    events.push({ at: m.at, order: 1, label: "maintenance · " + (m.satisfied ? "ready" : "not ready"),
      body: '<div class="crit">' + esc(m.reason || "") + ' · ' +
        esc((m.readiness || {}).detail || "") + '</div><div class="trace">' +
        esc(((m.usage || {}).notes || []).join("\n")) + '</div>' });
  });
  (r.session_schedule || []).forEach(function (s) {
    events.push({ at: s.at, order: 2,
      label: "checkpoint · " + s.event + (s.resumed ? " (resumed)" : " (new session)"),
      body: '<div class="crit">session ' + esc(s.session_id || "unknown") + '</div>' });
  });
  events.sort(function (a, b) { return a.at < b.at ? -1 : a.at > b.at ? 1 : a.order - b.order; });
  var note = '<div class="crit">Original bundle: ' + esc(r.bundle_file || "not captured") +
    ' · hash ' + esc(r.bundle_hash || "not captured") +
    '. Exact source observations: ' + esc(exposure.exposed ? "saved with this run" : "not captured") +
    '. Tool discovery, prefetch, and provider internal state: not captured in this historical run.</div>' +
    '<div class="crit">Provider usage: ' +
    esc(((r.adapter_usage || {}).notes || []).join(" · ")) +
    ' · exposed ' + esc((exposure.exposed || []).length) +
    ' · ingested ' + esc((exposure.ingested_ids || []).length) +
    '. Ingest/maintenance notes are recorded by the adapter; provider-internal memory state is not captured in these artifacts.</div>';
  return note + events.map(function (e) {
    return '<details class="timeline"><summary>' + esc(e.at) + ' · ' + esc(e.label) +
      '</summary>' + e.body + '</details>';
  }).join("");
}
function jsonBlock(x) {
  return '<pre class="answer">' + esc(x == null ? "not captured" : JSON.stringify(x, null, 2)) + '</pre>';
}
function toolEvidenceLinks(r, result) {
  var raw = String((result || {}).text || ""), seen = {}, ids = [];
  (r.observations || []).forEach(function (o) {
    if (raw.indexOf(o.source_id) >= 0) seen[o.source_id] = 1;
  });
  var match, lineIds = /"line_id"\s*:\s*(\d+)/g;
  while ((match = lineIds.exec(raw)) !== null) {
    var sid = (r.archive_sources || {})[match[1]];
    if (sid) seen[sid] = 1;
  }
  ids = Object.keys(seen);
  return ids.length ? '<div class="crit">Source messages: ' + ids.map(function (id) {
    return '<span class="evlink" data-ev="' + esc(id) + '">' + esc(id) + '</span>';
  }).join(' · ') + '</div>' : '<div class="crit">Source IDs not captured in this tool result</div>';
}
function readableToolResult(result) {
  if (!result) return null;
  var copy = {tool:result.tool, success:result.success, truncated:result.truncated};
  try {
    var body = JSON.parse(result.text);
    if (typeof body === "string") {
      try { body = JSON.parse(body); } catch (_) { /* keep text */ }
    }
    if (body && typeof body.result === "string") {
      try { body.result = JSON.parse(body.result); } catch (_) { /* keep text */ }
    }
    copy.body = body;
  } catch (_) { copy.body = result.text; }
  return copy;
}
function semanticView(r) {
  var events = [], observations = r.observations || [], contract = r.contract || {};
  observations.forEach(function (o) {
    events.push({at:o.written_at, order:0, label:"source · " + o.source_id,
      body:'<span class="evlink" data-ev="' + esc(o.source_id) + '">' + esc(o.source_id) +
        '</span> · ' + esc(o.sender) + ': ' + esc(o.text)});
  });
  var ingest = r.ingestion || {};
  if (r.ingestion) events.push({at:ingest.at, order:1, label:"ingestion · " +
    ((ingest.packet || []).length) + " observations", body:
    '<div class="crit">Exact packet</div>' + jsonBlock(ingest.packet) +
    '<div class="crit">Provider packets</div>' + jsonBlock(ingest.provider_packets) +
    '<div class="crit">Acknowledgment</div>' + jsonBlock(ingest.ack)});
  var maintenance = r.maintenance || {};
  if (r.maintenance) events.push({at:maintenance.at, order:2, label:"maintenance and readiness",
    body:jsonBlock(maintenance)});
  var turn = r.turn || {};
  if (r.preflight) events.push({at:turn.at || (r.status || {}).at || "after maintenance", order:3,
    label:"tool discovery and preflight", body:
      '<div class="crit">Retrieval seam</div>' + jsonBlock(r.retrieval) +
      '<div class="crit">Preflight</div>' + jsonBlock(r.preflight)});
  if (r.turn) {
    events.push({at:turn.at, order:4, label:"Hermes context and question", body:
      '<div class="crit">Exact prefetch context</div>' + jsonBlock(turn.prefetch_context) +
      '<div class="crit">Question and prompt</div>' + jsonBlock(turn.prompt)});
    (turn.tool_calls || []).forEach(function (call,i) {
      events.push({at:turn.at, order:5+i/100, label:"tool call · " + call.name,
        body:'<div class="crit">Arguments</div>' + jsonBlock(call.input) +
          '<div class="crit">Result</div>' + jsonBlock(readableToolResult((turn.tool_results || [])[i])) +
          toolEvidenceLinks(r, (turn.tool_results || [])[i])});
    });
    events.push({at:turn.at, order:6, label:"assistant answer", body:jsonBlock(turn.answer)});
  }
  if (r.judgment) events.push({at:turn.at || "after answer", order:7,
    label:"semantic judge request and response", body:
      '<div class="crit">Request</div>' + jsonBlock(r.judgment.prompt) +
      '<div class="crit">Response</div>' + jsonBlock(r.judgment.response) +
      '<div class="crit">Model call</div>' + jsonBlock(r.judgment.model_call)});
  events.sort(function (a,b) { return a.at < b.at ? -1 : a.at > b.at ? 1 : a.order-b.order; });
  var header = '<div class="crit">State: ' + esc((r.status || {}).state || "incomplete") +
    ' · ' + esc((r.status || {}).reason || "") +
    ' · actual Hindsight recall: ' + esc((r.status || {}).recall_called == null ? "not captured" : r.status.recall_called) +
    '<br>Frozen bundle: ' + esc(r.bundle_file) + ' · ' + esc(r.bundle_hash) +
    '<br>Frozen contract: ' + esc(contract.contract_hash || "not captured") +
    '</div>';
  document.getElementById("runtimeline").innerHTML = header + events.map(function (e) {
    return '<details class="timeline"><summary>' + esc(e.at) + ' · ' + esc(e.label) +
      '</summary>' + e.body + '</details>';
  }).join("");
  document.getElementById("gradetitle").textContent = r.dir + " · " +
    (contract.question || "contract not captured");
  document.getElementById("gradetable").innerHTML = '<div class="crit">' +
    esc(contract.review ? JSON.stringify(contract.review) : "contract review not captured") + '</div>' +
    '<table class="slots"><tr><th>claim</th><th>frozen statement</th><th>evidence</th><th>verdict</th><th>reason</th></tr>' +
    (contract.claims || []).map(function (c) {
      var g = ((r.grade || {}).claims || []).filter(function (x) { return x.id === c.id; })[0] || {};
      return '<tr><td>' + esc(c.id) + '</td><td>' + esc(c.statement) + '</td><td>' +
        (c.evidence || []).map(function (id) { return '<span class="evlink" data-ev="' + esc(id) + '">' + esc(id) + '</span>'; }).join('<br>') +
        '</td><td>' + pill(g.verdict || "ungraded") + '</td><td>' + esc(g.reason || "not captured") + '</td></tr>';
    }).join("") + '</table>';
  document.getElementById("turnstitle").textContent = "Answer and semantic judgment";
  document.getElementById("turns").innerHTML = '<div class="crit">Answer</div>' + jsonBlock(turn.answer) +
    '<div class="crit">Claim verdicts</div>' + jsonBlock(r.grade);
  Array.prototype.forEach.call(document.querySelectorAll(".evlink"), function (el) {
    el.onclick = function () { jumpToEvidence(el.getAttribute("data-ev")); };
  });
}
function renderRuns() {
  var host = document.getElementById("runcards");
  host.innerHTML = RUNS.map(function (r) {
    if (r.kind === "semantic") return '<div class="runcard' + (r.dir === RUNSEL ? " active" : "") +
      '" data-run="' + esc(r.dir) + '"><b>' + esc(r.life) + '</b> <span class="prov">' +
      esc(r.provider) + '</span><br>' + esc((r.status || {}).state || "incomplete") +
      ' · ' + esc(((r.grade || {}).correct || 0)) + '/' +
      esc(((r.contract || {}).claims || []).length) + ' claims<br><span class="prov">' +
      esc(r.dir) + '<br>bundle ' + esc(r.bundle_hash.slice(0,12)) + '</span></div>';
    var f1 = r.summary.f1;
    var n = r.grades.length;
    var nc = r.grades.filter(function (g) { return g.f1 === 1; }).length;
    return '<div class="runcard' + (r.dir === RUNSEL ? " active" : "") + '" data-run="' + esc(r.dir) + '">' +
      "<b>" + esc(r.life) + '</b> <span class="prov">' + esc(r.provider || "?") + "</span><br>" +
      '<span class="f1">' + fmtNum(f1) + "</span> F1 · " + nc + "/" + n + " checkpoints<br>" +
      '<span class="prov">' + esc(r.dir) + "<br>bundle " + esc((r.bundle_hash || "").slice(0, 12)) +
      (r.bundle_match === false ? ' <span class="bad">UNMATCHED</span>' : "") + "</span></div>";
  }).join("");
  Array.prototype.forEach.call(host.querySelectorAll(".runcard"), function (el) {
    el.onclick = function () { RUNSEL = el.getAttribute("data-run"); renderRuns(); };
  });
  var r = RUNS.filter(function (x) { return x.dir === RUNSEL; })[0] || RUNS[0];
  if (!r) return;
  RUNSEL = r.dir;
  var pm = PENDING.length ? " · pending (no grades yet): " + PENDING.join(", ") : "";
  document.getElementById("runmeta").textContent =
    "built " + META.generated_at + " · " + META.obs_total + " observations · " + pm;
  if (r.kind === "semantic") { semanticView(r); return; }
  document.getElementById("runtimeline").innerHTML = runTimeline(r);
  document.getElementById("gradetitle").textContent =
    r.dir + " — " + r.life + " / " + r.provider + " (bundle " + (r.bundle_hash || "").slice(0, 12) + ")";
  var gh = '<table class="grades"><tr><th>checkpoint</th><th>question</th><th>F1</th>' +
    "<th>slots</th><th>class</th><th>detail</th></tr>";
  r.grades.forEach(function (g, i) {
    var crit = g.criteria || {};
    var bad = g.f1 !== 1;
    var minipills = (g.slots || []).map(function (s) { return pill(s.verdict); }).join(" ") || "—";
    var cls = (g.class_category == null ? "—" : "cat " + g.class_category) +
      (g.class_basis && g.class_basis !== "none" ? " · " + esc(g.class_basis) : "");
    gh += '<tr class="' + (bad ? "fail" : "pass") + '"><td>' + esc(String(g.checkpoint).split("/")[1] || g.checkpoint) +
      '</td><td>' + esc(crit.question || "") + "</td><td><b>" + fmtNum(g.f1) + "</b></td>" +
      "<td>" + minipills + "</td><td>" + cls + "</td>" +
      '<td><details class="gdetail"><summary>expected vs answered vs evidence</summary>' +
      '<div class="crit">mode ' + esc(crit.mode || "?") + " · family " + esc(crit.family || "?") +
      (crit.required_fields && crit.required_fields.length ? " · required: " + esc(crit.required_fields.join(", ")) : "") +
      (crit.source ? " · rubric: " + esc(crit.source) : "") + "</div>" +
      slotRows(g) +
      '<div class="crit"><b>agent answer:</b></div><div class="answer">' + esc(g.answer || "(none)") + "</div>" +
      (g.class_reason ? '<div class="crit">failure analysis: ' + esc(g.class_reason) + "</div>" : "") +
      "</details></td></tr>";
  });
  document.getElementById("gradetable").innerHTML = gh + "</table>";
  Array.prototype.forEach.call(document.querySelectorAll("#gradetable .evlink"), function (el) {
    el.onclick = function () { jumpToEvidence(el.getAttribute("data-ev")); };
  });
  Array.prototype.forEach.call(document.querySelectorAll("#runtimeline .evlink"), function (el) {
    el.onclick = function () { jumpToEvidence(el.getAttribute("data-ev")); };
  });
  document.getElementById("turnstitle").textContent = "turns (" + r.turns.length + ")";
  document.getElementById("turns").innerHTML = r.turns.map(function (t) {
    var tools = (t.tool_calls || []).map(function (c) { return esc(c.name); }).join(", ") || "—";
    return '<div class="turn"><div class="thead"><span class="kind ' + esc(t.kind) + '">' + esc(t.kind) +
      '</span><span class="prompt">' + esc((t.prompt || "").slice(0, 120)) + "</span>" +
      '<span class="tools">' + esc(t.at || "") + " · tools: " + tools + "</span></div>" +
      '<details><summary>prompt, answer + tool detail</summary><div class="crit">full prompt</div><div class="answer">' +
      esc(t.prompt || "(none)") + '</div><div class="crit">answer</div><div class="answer">' +
      esc(t.answer || "(none)") + "</div>" +
      '<div class="crit">latency ' + esc(t.latency_s) + "s · returncode " + esc(t.returncode) +
      " · in/out tokens " + esc(t.turn_input_tokens) + "/" + esc(t.turn_output_tokens) + "</div>" +
      (t.tool_calls || []).map(function (tc) {
        return '<div class="crit"><b>call ' + esc(tc.name) + '</b></div><div class="answer">' +
          esc(JSON.stringify(tc.input, null, 2) || "(no input)") + '</div>';
      }).join("") +
      (t.tool_results || []).map(function (tr) {
        return '<div class="crit"><b>' + esc(tr.tool) + "</b> success=" + esc(tr.success) +
          (tr.truncated ? " (truncated)" : "") + "</div>" +
          '<div class="answer">' + esc(tr.text || "") + "</div>";
      }).join("") + "</details></div>";
  }).join("");
}

/* ---------- init ---------- */
function initCorpusControls() {
  var l = document.getElementById("life");
  l.innerHTML = Object.keys(CORPUS).sort().map(function (k) {
    return "<option>" + esc(k) + "</option>";
  }).join("");
  S.life = l.value; S.day = days()[0][0];
  l.onchange = function () { S.life = l.value; S.thr = "all"; S.day = days()[0][0]; renderCorpus(); };
  document.getElementById("day").onchange = function (e) { S.day = e.target.value; renderCorpus(); };
  document.getElementById("prev").onclick = function () {
    var d = days().map(function (x) { return x[0]; }), i = d.indexOf(S.day);
    if (i > 0) { S.day = d[i - 1]; renderCorpus(); }
  };
  document.getElementById("next").onclick = function () {
    var d = days().map(function (x) { return x[0]; }), i = d.indexOf(S.day);
    if (i < d.length - 1) { S.day = d[i + 1]; renderCorpus(); }
  };
  document.getElementById("chan").onchange = function (e) { S.chan = e.target.value; renderCorpus(); };
  document.getElementById("thr").onchange = function (e) { S.thr = e.target.value; renderCorpus(); };
  document.getElementById("q").oninput = function (e) { S.q = e.target.value; renderCorpus(); };
}
Promise.all([
  fetch("data/manifest.json", {cache:"no-store"}).then(function (r) { return r.json(); }),
  fetch("data/corpus.json", {cache:"no-store"}).then(function (r) { return r.json(); }),
  fetch("data/runs.json", {cache:"no-store"}).then(function (r) { return r.json(); })
]).then(function (parts) {
  META = parts[0]; CORPUS = parts[1];
  RUNS = parts[2].runs; PENDING = parts[2].pending || [];
  RUNSEL = RUNS.length ? RUNS[0].dir : null;
  document.getElementById("h-meta").textContent =
    "built " + META.generated_at + " · " + META.obs_total + " obs · " +
    META.bundles.map(function (b) { return b.file + " " + b.hash.slice(0, 8); }).join(" · ");
  document.getElementById("tab-corpus").onclick = function () { showTab("corpus"); };
  document.getElementById("tab-runs").onclick = function () { showTab("runs"); };
  initCorpusControls();
  renderCorpus();
  renderRuns();
}).catch(function (e) {
  document.getElementById("view").innerHTML =
    "<p>Failed to load local data/*.json — serve via serve_explorer.py, not file://. (" +
    esc(e.message) + ")</p>";
});
