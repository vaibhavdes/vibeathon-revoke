"use strict";

const CATEGORY = {
  leaver_active_access: "Leaver still has access",
  post_termination_activity: "Used after leaving",
  upcoming_leaver: "Leaving soon (notice period)",
  unknown_identity: "No HR record",
  service_account_owner: "Service account without active owner",
  mover_retained_privilege: "Kept role from previous job",
  sod_conflict: "Segregation-of-duties conflict",
  dormant_account: "Dormant (unused 90+ days)",
  shared_account: "Shared team login",
};
const SEVERITY = { critical: 0, high: 1, medium: 2, low: 3 };
const SYSTEM_LABEL = { peoplehub: "PeopleHub", ledgerly: "Ledgerly" };

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// Each tab gets its own id, so replays and answers go only to the tab that started them.
const CLIENT = (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2));
const ui = { replaying: false, busy: false, working: null, steps: null, toolCalls: 0, decided: [] };

function md(text) {
  if (window.marked && window.DOMPurify) return DOMPurify.sanitize(marked.parse(text || ""));
  return `<p>${esc(text).replace(/\n/g, "<br>")}</p>`;
}

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(body.detail || res.statusText), { status: res.status });
  return body;
}

/* ---------- tabs ---------- */
document.querySelectorAll(".tabs button").forEach((b) =>
  b.addEventListener("click", () => showTab(b.dataset.tab)));
function showTab(name) {
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".panel").forEach((p) => p.classList.toggle("active", p.id === `panel-${name}`));
}

/* ---------- state ---------- */
function renderModelChip(m) {
  const chip = $("#chip-model");
  if (!chip || !m) return;
  const limited = Object.entries(m.limited || {});
  const configured = m.configured || [];
  const free = configured.filter((c) => !(m.limited || {})[c]);
  chip.classList.toggle("warn", configured.length > 0 && free.length === 0);
  if (!configured.length) { chip.textContent = "Model: none · replay only"; chip.title = "No model key on this server. Recorded runs replay without one."; return; }
  if (!free.length) {
    const back = Math.min(...limited.map(([, t]) => t));
    const at = new Date(back * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    chip.textContent = `Model limit reached · back at ${at}`;
  } else {
    chip.textContent = `Model: ${free[0].split(":").pop()}${limited.length ? ` (${limited.length} at daily limit)` : ""}`;
  }
  chip.title = configured.map((c) => (m.limited || {})[c]
    ? `${c}: daily limit, back at ${new Date(m.limited[c] * 1000).toLocaleTimeString()}` : `${c}: available`).join("\n");
}

async function refreshState() {
  let s;
  try { s = await api("/api/state"); } catch (e) { return; }
  for (const sys of s.systems) {
    const chip = $(`#chip-${sys.key}`);
    chip.classList.toggle("up", sys.up);
    chip.classList.toggle("down", !sys.up);
    chip.title = sys.up ? `${sys.url} is up` : `${sys.url} is not responding: run python -m scripts.run_systems`;
  }
  const byNs = { peoplehub: 0, ledgerly: 0 };
  s.tools.forEach((t) => { byNs[t.system] = (byNs[t.system] || 0) + 1; });
  $("#chip-tools").textContent = s.tools_error ? "MCP tools: unavailable"
    : `MCP tools: ${s.tools.length} (PeopleHub ${byNs.peoplehub} · Ledgerly ${byNs.ledgerly})`;
  $("#chip-date").textContent = `Review date ${s.review_date}`;
  renderModelChip(s.model_limits);
  setCanAsk(s.can_ask);
  $("#btn-live").classList.toggle("hidden", s.live_update_applied);
  $("#btn-unlive").classList.toggle("hidden", !s.live_update_applied);
  renderSystems(s);
  renderPending(s.pending);
  setBusy(s.busy || ui.replaying);
}

function renderSystems(s) {
  const live = new Set(s.tools.map((t) => t.endpoint));
  $("#systems").innerHTML = s.systems.map((sys) => {
    const rows = sys.expose.map((e) => `
      <tr><td class="ep">${esc(e.endpoint)}</td>
        <td><span class="acc ${e.access}">${e.access}</span></td>
        <td><code>${esc(e.tool)}</code><div class="why">${esc(e.why)}</div></td></tr>`).join("");
    const liveRows = sys.live_demo.map((e) => `
      <tr><td class="ep">${esc(e.endpoint)}</td>
        <td><span class="acc ${live.has(e.endpoint) ? "new" : "withheld"}">${live.has(e.endpoint) ? "new" : "not yet"}</span></td>
        <td><code>${esc(e.tool)}</code><div class="why">${esc(e.why)}</div></td></tr>`).join("");
    const withheld = sys.withheld.map((e) => `
      <tr class="withheld"><td class="ep">${esc(e.endpoint)}</td>
        <td><span class="acc withheld">withheld</span></td><td class="why">${esc(e.why)}</td></tr>`).join("");
    return `<div class="sys-card"><header><b>${SYSTEM_LABEL[sys.key]}</b>
      <span>${esc(sys.server_file || "not built")}</span></header>
      <table>${rows}${liveRows}${withheld}</table></div>`;
  }).join("");
}

/* ---------- conversation ---------- */
function addMessage(cls, html) {
  const div = document.createElement("div");
  div.className = `msg ${cls}`;
  div.innerHTML = html;
  $("#messages").appendChild(div);
  div.scrollIntoView({ behavior: "smooth", block: "end" });
  return div;
}

function setBusy(busy) {
  ui.busy = busy;
  $("#send").disabled = busy || ui.canAsk === false;
  $("#send").textContent = busy ? "Working…" : "Ask";
  document.querySelectorAll("[data-replay], #btn-live, #btn-unlive, #btn-reset, #btn-connect").forEach((b) => (b.disabled = busy));
  document.querySelectorAll("#suggest button").forEach((b) => (b.disabled = busy || ui.canAsk === false));
}

// Without a model key (e.g. a shared instance) live questions are off; recorded runs still replay.
function setCanAsk(canAsk) {
  if (ui.canAsk === canAsk) return;
  ui.canAsk = canAsk;
  $("#question").disabled = !canAsk;
  $("#question").placeholder = canAsk ? "Ask about access in Ledgerly…"
    : "Questions need a model key (run it with your own). Replay a past run above.";
  $("#intro").classList.toggle("replay-only", !canAsk);
  // With a model, the questions above run live and past runs stay out of the way.
  $("#recordings").classList.toggle("hidden", canAsk || !ui.recordings.length);
  const note = $("#recordings p");
  if (note) note.textContent = "This copy has no model key, so it replays past runs from their evidence packs: the same events, findings and approvals.";
  setBusy(ui.busy);
}

function startWorking(question, label) {
  $("#intro")?.classList.add("hidden");
  addMessage("user", (label ? `<span class="tag">${esc(label)}</span> ` : "") + esc(question));
  ui.working = addMessage("agent working", `<span class="dots">Reviewing</span><ol class="steps"></ol>`);
  ui.steps = ui.working.querySelector(".steps");
  ui.toolCalls = 0;
  setBusy(true);
}

async function ask(question) {
  question = question.trim();
  if (!question || ui.busy) return;
  startWorking(question);
  try {
    await api(`/api/ask?client=${CLIENT}`, { method: "POST", body: JSON.stringify({ question }) });
  } catch (e) {
    ui.working.remove();
    ui.working = null;
    if (e.status === 429) showLimit(e.message);
    else addMessage("agent error", esc(e.message));
    setBusy(false);
  }
}

// Recorded runs: the console events of real runs, kept in the evidence pack.
// Replaying one needs no model, so the demo still works when the free quota is used up.
const QUESTION_LABEL = {};
document.querySelectorAll("#suggest button").forEach((b) => (QUESTION_LABEL[b.dataset.q] = b.textContent));
ui.recordings = [];

function recordingButtons() {
  const seen = new Set();
  const order = Object.keys(QUESTION_LABEL);
  const rank = (q) => (order.indexOf(q) + 1) || 99;
  return ui.recordings.filter((r) => r.source === "sample" && !seen.has(r.question) && seen.add(r.question))
    .sort((a, b) => rank(a.question) - rank(b.question))
    .map((r) => `<button type="button" data-replay="${esc(r.id)}" data-question="${esc(r.question)}">▶ ${esc(QUESTION_LABEL[r.question] || r.question.slice(0, 48))}</button>`)
    .join("");
}

async function loadRecordings() {
  try { ui.recordings = (await api("/api/recordings")).recordings; } catch (e) { return; }
  const html = recordingButtons();
  $("#recordings").classList.toggle("hidden", !html || ui.canAsk !== false);
  $("#recordings .suggest").innerHTML = html;
}

function showLimit(message) {
  const replays = recordingButtons();
  addMessage("agent limit", `<strong>Model limit reached.</strong> ${esc(message)}` +
    (replays ? `<p class="muted">Meanwhile, replay a recorded run (no model needed):</p><div class="suggest">${replays}</div>` : ""));
  refreshState();
}

async function replay(id, question) {
  if (ui.busy) return;
  startWorking(question, "Past run");
  ui.replaying = true;
  try {
    const speed = new URLSearchParams(location.search).get("replay_speed") || "1";
    await api(`/api/replay/${encodeURIComponent(id)}?speed=${encodeURIComponent(speed)}&client=${CLIENT}`, { method: "POST" });
  } catch (e) {
    ui.replaying = false;
    ui.working.remove();
    ui.working = null;
    addMessage("agent error", esc(e.message));
    setBusy(false);
  }
}

$("#composer").addEventListener("submit", (e) => {
  e.preventDefault();
  const q = $("#question").value;
  $("#question").value = "";
  ask(q);
});
$("#question").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#composer").requestSubmit(); }
});
document.querySelectorAll("#suggest button").forEach((b) => b.addEventListener("click", () => ask(b.dataset.q)));
document.addEventListener("click", (e) => {
  const r = e.target.closest("[data-replay]");
  if (r) { replay(r.dataset.replay, r.dataset.question); return; }
  const b = e.target.closest("[data-q]");
  if (b && !b.closest("#suggest")) ask(b.dataset.q);
});

/* ---------- trace ---------- */
function traceItem(sysClass, sysLabel, body, cls = "") {
  const li = document.createElement("li");
  li.className = cls;
  li.innerHTML = `<span class="sys ${sysClass}">${esc(sysLabel)}</span><div>${body}</div>`;
  $("#trace").appendChild(li);
  li.scrollIntoView({ block: "nearest" });
  return li;
}
function step(text) {
  if (!ui.steps) return;
  const li = document.createElement("li");
  li.innerHTML = text;
  ui.steps.appendChild(li);
}
const pendingCalls = [];

/* ---------- findings ---------- */
function factsHtml(f) {
  const g = f.facts || {};
  if (!g.verified) return `<span class="unverified">Unverified: no tool returned this account</span>`;
  const parts = [`Ledgerly: ${esc(g.account_status)} ${esc(g.account_type || "")}, ${esc((g.roles || []).join(", ") || "no roles")}, last sign-in ${esc((g.last_login || "never").slice(0, 10))}`];
  if (g.owner) parts.push(`owner: ${esc(g.owner)}`);
  if (g.hr_status) {
    let hr = `PeopleHub: ${esc(g.hr_status)}`;
    if (g.title) hr += `, ${esc(g.title)}`;
    if (g.termination_date) hr += `, last day ${esc(g.termination_date)}`;
    if (g.rehire_date) hr += `, rehired ${esc(g.rehire_date)}`;
    if (g.leave_until) hr += `, on leave until ${esc(g.leave_until)}`;
    parts.push(hr);
  }
  let html = parts.join("<br>");
  if (g.signed_in_after_leaving) html += `<span class="after">Signed in after the last day: ${esc(g.signed_in_after_leaving.replace("T", " ").replace("Z", ""))}</span>`;
  for (const e of g.used_after_leaving || []) {
    const amount = e.amount_usd ? ` USD ${Number(e.amount_usd).toLocaleString()}` : "";
    html += `<span class="after">${esc(e.ts.replace("T", " ").replace("Z", ""))} ${esc(e.action)}${amount}: ${esc(e.detail || "")}</span>`;
  }
  return html;
}

function renderFindings(findings, runId) {
  const sorted = [...findings].sort((a, b) =>
    (SEVERITY[a.severity] ?? 9) - (SEVERITY[b.severity] ?? 9) || String(a.category).localeCompare(b.category));
  $("#n-findings").textContent = findings.length;
  const counts = {};
  findings.forEach((f) => (counts[f.severity] = (counts[f.severity] || 0) + 1));
  $("#sev-summary").innerHTML = Object.keys(SEVERITY).filter((s) => counts[s])
    .map((s) => `<span class="pill ${s}">${counts[s]} ${s}</span>`).join("");
  $("#findings").innerHTML = sorted.length ? sorted.map((f) => {
    const extra = [f.role, f.rule_id].filter(Boolean).join(" ");
    return `<tr>
      <td><span class="pill ${esc(f.severity)}">${esc(f.severity)}</span></td>
      <td>${esc(CATEGORY[f.category] || f.category)}${extra ? `<div class="muted">${esc(extra)}</div>` : ""}</td>
      <td class="acct">${esc(f.username)}<div class="muted" style="font-family:inherit;font-weight:400">${esc(f.person || "")}</div></td>
      <td class="facts">${factsHtml(f)}</td>
      <td>${esc(f.certifier || "")}</td>
      <td>${esc(f.recommended_action || "")}</td></tr>`;
  }).join("") : `<tr class="empty"><td colspan="6">No findings for this question.</td></tr>`;
  const link = $("#evidence-link");
  if (runId) { link.href = `/evidence/${runId}`; link.classList.remove("hidden"); }
}

/* ---------- approvals ---------- */
function renderPending(pending) {
  $("#n-pending").textContent = pending.length;
  $("#n-pending").dataset.n = pending.length;
  $("#approve-bar").classList.toggle("hidden", !pending.length);
  $("#pending").innerHTML = pending.length ? pending.map((p) => `
    <label class="proposal"><input type="checkbox" value="${esc(p.id)}" checked>
      <div><div class="what">${esc(p.id)} · ${esc(p.summary)}</div>
        <div class="why">${esc(p.arguments.reason || "")}</div>
        <div class="call">${esc(p.tool)}(${esc(JSON.stringify(p.arguments))})</div></div></label>`).join("")
    : `<p class="muted">No changes waiting. Ask Revoke to fix something, e.g. “Disable the leavers' accounts.”</p>`;
}

function renderDecided() {
  $("#decided").innerHTML = ui.decided.length ? ui.decided.map((p) => {
    const ok = p.status === "executed";
    const cls = p.status === "rejected" ? "muted" : ok ? "ok" : "bad";
    return `<div class="decided"><b>${esc(p.id)}</b> ${esc(p.summary)}
      <span class="${cls}">${esc(p.status)}</span>
      <span class="muted">${esc(p.verification?.summary || "")} · ${esc(p.decided_by)} ${esc(p.decided_at)}</span></div>`;
  }).join("") : `<p class="muted">Nothing decided yet.</p>`;
}

async function decide(approve) {
  const ids = [...document.querySelectorAll("#pending input:checked")].map((i) => i.value);
  if (!ids.length) return;
  const approver = $("#approver").value.trim();
  if (approve && !approver) { $("#approver").focus(); $("#approver").placeholder = "Name required to approve"; return; }
  $("#btn-approve").disabled = $("#btn-reject").disabled = true;
  try {
    await api("/api/decide", { method: "POST", body: JSON.stringify({ ids, approve, approver: approver || "console user" }) });
  } catch (e) { alert(e.message); }
  $("#btn-approve").disabled = $("#btn-reject").disabled = false;
  refreshState();
}
$("#btn-approve").addEventListener("click", () => decide(true));
$("#btn-reject").addEventListener("click", () => decide(false));

/* ---------- live update / reset ---------- */
async function liveUpdate(action) {
  const term = $("#terminal");
  term.classList.remove("hidden");
  term.textContent = action === "apply"
    ? "$ python -m scripts.live_update apply\n$ python -m scripts.build_mcp --system ledgerly --discover --include-live\n…"
    : "$ python -m scripts.live_update remove\n$ python -m scripts.build_mcp --system ledgerly --discover\n…";
  setBusy(true);
  try {
    const r = await api(`/api/live-update/${action}`, { method: "POST" });
    term.textContent += "\n" + r.output;
    if (action === "apply") traceItem("code", "Synapse", `<span class="tool">synapse build --discover</span>
      <div class="res">${r.ok ? "Ledgerly rebuilt: get_account_activity is now a tool" : "build failed"}</div>`);
  } catch (e) { term.textContent += "\n" + e.message; }
  setBusy(false);
  refreshState();
}
$("#btn-live").addEventListener("click", () => liveUpdate("apply"));
$("#btn-connect").addEventListener("click", async () => {
  const term = $("#terminal");
  term.classList.remove("hidden");
  term.textContent = "$ python -m scripts.build_mcp\n…";
  setBusy(true);
  try {
    const r = await api("/api/connectors/rebuild", { method: "POST" });
    term.textContent = "$ python -m scripts.build_mcp\n" + r.output;
  } catch (e) { term.textContent += "\n" + e.message; }
  setBusy(false);
  refreshState();
});
$("#btn-unlive").addEventListener("click", () => liveUpdate("remove"));
$("#btn-reset").addEventListener("click", async () => {
  if (!confirm("Restore the seed data in both systems and remove the live update?")) return;
  setBusy(true);
  await api("/api/reset", { method: "POST" }).catch((e) => alert(e.message));
  location.reload();
});
$("#btn-new").addEventListener("click", async () => {
  await api("/api/new-conversation", { method: "POST" });
  location.reload();
});

/* ---------- events from the agent ---------- */
function onEvent(e) {
  switch (e.type) {
    case "start":
      traceItem("", "", esc(e.question), "question");
      break;
    case "tools":
      break;
    case "tool_call":
      ui.toolCalls += 1;
      pendingCalls.push(traceItem(e.system, SYSTEM_LABEL[e.system] || e.system,
        `<span class="tool">${esc(e.tool.split("__")[1])}</span> <span class="args">${esc(JSON.stringify(e.arguments).slice(0, 160))}</span><div class="res muted">…</div>`));
      step(`${esc(SYSTEM_LABEL[e.system])}: <code>${esc(e.tool.split("__")[1])}</code>`);
      break;
    case "tool_result": {
      const li = pendingCalls.shift();
      if (li) {
        const res = li.querySelector(".res");
        res.className = `res ${e.ok ? "" : "bad"}`;
        res.textContent = `${e.ok ? "✓" : "✗"} ${e.summary} · ${e.ms} ms`;
      }
      break;
    }
    case "join":
      traceItem("code", "join", `Joined ${e.rows} Ledgerly accounts to their PeopleHub records by email, in code`, "note");
      step(`joined ${e.rows} accounts to HR records`);
      break;
    case "findings":
      traceItem("agent", "agent", `Recorded ${e.findings.length} verified findings`);
      for (const r of e.rejected || []) {
        traceItem("code", "verify", `<span class="res bad">Rejected:</span> ${esc(CATEGORY[r.category] || r.category)} for <code>${esc(r.username)}</code>: ${esc(r.rejected_because)}`, "note");
      }
      step(`recorded ${e.findings.length} verified findings${(e.rejected || []).length ? `, rejected ${e.rejected.length}` : ""}`);
      break;
    case "completeness": {
      const items = Object.entries(e.missed).map(([c, us]) => `${esc(CATEGORY[c] || c)}: ${us.map((u) => `<code>${esc(u)}</code>`).join(", ")}`);
      traceItem("code", "verify", `<span class="res">Completeness check, also matching the rule:</span> ${items.join("; ")}`, "note");
      break;
    }
    case "proposal":
      traceItem("agent", "proposal", `Queued for approval: ${esc(e.proposal.summary)}`);
      break;
    case "blocked":
      traceItem("agent", "blocked", `<span class="res bad">Blocked by policy:</span> ${esc(e.message)}`);
      break;
    case "model_switch":
      traceItem("", "", `${esc(e.from)} unavailable (${esc(e.reason)}): switching model`, "note");
      break;
    case "rate_limit_wait":
      traceItem("", "", `Free-tier rate limit: waiting ${esc(e.seconds)} s`, "note");
      break;
    case "final": {
      ui.working?.remove();
      ui.working = null;
      const n = e.findings.length, p = e.proposals.length;
      const meta = [`${ui.toolCalls} tool calls`];
      if (n) meta.push(`<a href="#" data-tab-link="findings">${n} findings</a>`);
      if (p) meta.push(`<a href="#" data-tab-link="approvals">${p} changes waiting for approval</a>`);
      meta.push(`<a href="/evidence/${esc(e.run_id)}" target="_blank">evidence pack</a>`);
      if (e.replay) meta.push("past run, replayed from its evidence pack");
      addMessage("agent", `<div class="md">${md(e.answer)}</div><div class="meta">${meta.join("")}</div>`);
      if (n) renderFindings(e.findings, e.run_id);
      refreshState();
      if (p && !e.replay) showTab("approvals"); else if (n) showTab("findings");
      break;
    }
    case "decision":
      ui.decided.unshift(e.proposal);
      renderDecided();
      traceItem("ledgerly", "Ledgerly", `<span class="tool">${esc(e.proposal.tool.split("__")[1])}</span>
        <span class="args">${esc(JSON.stringify(e.proposal.arguments).slice(0, 120))}</span>
        <div class="res ${e.proposal.status === "failed" ? "bad" : ""}">${esc(e.proposal.status)} ${esc(e.proposal.verification?.summary || "")}</div>`);
      break;
    case "limit":
      ui.working?.remove();
      ui.working = null;
      showLimit(e.message);
      break;
    case "error":
      ui.working?.remove();
      ui.working = null;
      addMessage("agent error", esc(e.message));
      break;
    case "idle":
      ui.replaying = false;
      setBusy(false);
      break;
  }
}
document.addEventListener("click", (e) => {
  const a = e.target.closest("[data-tab-link]");
  if (a) { e.preventDefault(); showTab(a.dataset.tabLink); }
});

const events = new EventSource(`/api/events?client=${CLIENT}`);
events.onmessage = (m) => onEvent(JSON.parse(m.data));

refreshState();
loadRecordings();
