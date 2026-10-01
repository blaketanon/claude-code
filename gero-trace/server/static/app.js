/* Gero Trace admin page. Plain JS, no build step. */
const $ = s => document.querySelector(s);
let csrf = null, approveLabel = "approved-for-fix", current = null, timer = null;

async function api(path, opts = {}) {
  const headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
  if (csrf) headers["X-CSRF-Token"] = csrf;
  const r = await fetch("/api" + path, Object.assign({}, opts, { headers, body: opts.body ? JSON.stringify(opts.body) : undefined }));
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.message || r.statusText);
  return data;
}

const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const when = iso => iso ? new Date(iso).toLocaleString("en-US", { timeZone: "America/New_York", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "";
const pill = v => v ? `<span class="pill ${esc(v)}">${esc(v).replace(/_/g, " ")}</span>` : "";
const money = v => v == null ? "" : "$" + Number(v).toFixed(2);

async function boot() {
  const me = await api("/me");
  if (me.admin) { csrf = me.csrf; approveLabel = me.approve_label || approveLabel; showApp(); } else showLogin();
}

function showLogin() { $("#login").hidden = false; $("#app").hidden = true; $("#nav").hidden = true; }
function showApp() { $("#login").hidden = true; $("#app").hidden = false; $("#nav").hidden = false; route(); }

$("#loginForm").addEventListener("submit", async e => {
  e.preventDefault();
  $("#loginError").textContent = "";
  try { const r = await api("/login", { method: "POST", body: { password: $("#password").value } }); csrf = r.csrf; showApp(); }
  catch (err) { $("#loginError").textContent = err.message; }
});
$("#logout").addEventListener("click", async () => { await api("/logout", { method: "POST" }); csrf = null; showLogin(); });
$("#refresh").addEventListener("click", loadList);
$("#search").addEventListener("input", debounce(loadList, 300));
$("#filterStatus").addEventListener("change", loadList);
$("#filterFix").addEventListener("change", loadList);
$("#back").addEventListener("click", () => { location.hash = "#/"; });
window.addEventListener("hashchange", route);

function debounce(fn, ms) { let t; return () => { clearTimeout(t); t = setTimeout(fn, ms); }; }

function route() {
  clearInterval(timer);
  const m = location.hash.match(/^#\/investigations\/([a-f0-9]+)/);
  if (m) { $("#list").hidden = true; $("#detail").hidden = false; loadDetail(m[1]); timer = setInterval(() => loadDetail(m[1], true), 5000); }
  else { $("#detail").hidden = true; $("#list").hidden = false; loadList(); loadStats(); timer = setInterval(() => { loadList(true); loadStats(); }, 15000); }
}

async function loadStats() {
  try {
    const s = await api("/admin/stats");
    $("#stats").textContent = `${s.running} running · ${s.queued} queued · ${s.awaiting_approval} awaiting approval · ${s.pr_open} PRs open`;
  } catch (e) { /* ignore */ }
}

async function loadList(quiet) {
  const p = new URLSearchParams();
  if ($("#search").value) p.set("q", $("#search").value);
  if ($("#filterStatus").value) p.set("status", $("#filterStatus").value);
  if ($("#filterFix").value) p.set("fix_status", $("#filterFix").value);
  try {
    const { items } = await api("/admin/investigations?" + p);
    $("#rows").innerHTML = items.map(i => `
      <tr data-id="${i.id}">
        <td>${when(i.created_at)}</td>
        <td>${esc((i.asked_by || {}).name || "")}</td>
        <td class="q" title="${esc(i.question)}">${esc(i.question)}<br><span class="muted">${esc(i.record_object || "")} ${esc(i.record_id || "")}</span></td>
        <td>${pill(i.status)}</td>
        <td>${pill(i.confidence)}</td>
        <td>${i.issue ? `<a href="${esc(i.issue.url)}" target="_blank" rel="noopener">#${i.issue.number}</a>` : ""}</td>
        <td>${i.fix ? pill(i.fix.status) : ""}${i.fix && i.fix.pr_url ? ` <a href="${esc(i.fix.pr_url)}" target="_blank" rel="noopener">PR</a>` : ""}</td>
        <td>${money(i.cost_usd)}</td>
      </tr>`).join("") || `<tr><td colspan="8" class="muted">No investigations yet.</td></tr>`;
    document.querySelectorAll("#rows tr[data-id]").forEach(tr => tr.addEventListener("click", e => {
      if (e.target.tagName === "A") return;
      location.hash = "#/investigations/" + tr.dataset.id;
    }));
  } catch (e) { if (!quiet) alert(e.message); }
}

async function loadDetail(id, quiet) {
  try {
    const d = await api("/admin/investigations/" + id);
    current = d;
    render(d);
  } catch (e) { if (!quiet) $("#detailBody").innerHTML = `<p class="error">${esc(e.message)}</p>`; }
}

function render(d) {
  const f = d.findings || {};
  const fix = f.proposed_fix || {};
  const canApprove = d.status === "reported" && ["awaiting_approval", "declined", "failed"].includes(d.fix_status || "awaiting_approval") || (d.status === "answered" && f.needs_fix);
  const canDecline = ["awaiting_approval", "approved"].includes(d.fix_status);
  $("#detailBody").innerHTML = `
    <div class="detail">
      <h2>${esc(f.title || d.question.slice(0, 100))}</h2>
      <div class="meta">
        ${pill(d.status)} ${pill(d.confidence)} ${d.fix_status ? "fix: " + pill(d.fix_status) : ""} · asked ${when(d.created_at)} by ${esc((d.asked_by || {}).name || "?")}
        ${d.record_url ? ` · <a href="${esc(d.record_url)}" target="_blank" rel="noopener">${esc(d.record_object || "record")} ${esc(d.record_id || "")}</a>` : ""}
        ${d.issue ? ` · <a href="${esc(d.issue.url)}" target="_blank" rel="noopener">issue #${d.issue.number}</a>` : ""}
        ${d.fix && d.fix.pr_url ? ` · <a href="${esc(d.fix.pr_url)}" target="_blank" rel="noopener">pull request</a>` : ""}
        · ${money(d.cost_usd)}${d.fix_cost_usd ? " + " + money(d.fix_cost_usd) + " fix" : ""}${d.num_turns ? " · " + d.num_turns + " turns" : ""}
      </div>
      <div class="actions">
        ${canApprove ? `<button id="approve">Approve: let Claude open a fix PR</button>` : ""}
        ${canDecline ? `<button id="decline" class="danger">Decline fix</button>` : ""}
        ${d.status === "failed" ? `<button id="retry" class="secondary">Retry investigation</button>` : ""}
        ${d.fix_status === "failed" ? `<button id="retryFix" class="secondary">Retry fix</button>` : ""}
        ${d.issue ? "" : (d.status === "answered" ? `<span class="muted">Not reported to engineering yet (the user can do that from Salesforce). Approving here opens a PR without an issue.</span>` : "")}
      </div>
      <div class="cols">
        <div>
          <h3>Question</h3><p>${esc(d.question)}</p>
          ${d.reporter_comment ? `<h3>Reporter's comment</h3><p>${esc(d.reporter_comment)}</p>` : ""}
          <h3>Answer shown to the user</h3>
          <div class="answer">${d.answer_html || `<span class="muted">${esc(d.progress || d.error || "no answer yet")}</span>`}</div>
          ${d.error ? `<p class="error">${esc(d.error)}</p>` : ""}
          ${fix.summary ? `<h3>Proposed fix</h3><p><b>${esc(fix.repo || "")}</b> · risk ${esc(fix.risk || "?")}</p><p>${esc(fix.summary)}</p>` : ""}
          ${d.fix_error ? `<p class="error">Fix: ${esc(d.fix_error)}</p>` : ""}
          ${d.report_markdown ? `<h3>Engineering report</h3><pre class="report">${esc(d.report_markdown)}</pre>` : ""}
        </div>
        <div>
          <h3>What Claude did</h3>
          <div class="events">${(d.events || []).map(e => `<div class="${esc(e.kind)}"><span class="muted">${when(e.at)}</span> ${esc(e.phase)} · ${esc(e.text)}</div>`).join("") || "<span class='muted'>nothing yet</span>"}</div>
        </div>
      </div>
    </div>`;
  const on = (sel, fn) => { const el = $(sel); if (el) el.addEventListener("click", fn); };
  on("#approve", async () => { if (confirm(`Let Claude implement the fix and open a draft pull request in ${fix.repo || "the repo"}?`)) act("/approve"); });
  on("#decline", async () => { const reason = prompt("Reason (optional)") || ""; act("/decline", { reason }); });
  on("#retry", () => act("/retry", { what: "investigation" }));
  on("#retryFix", () => act("/retry", { what: "fix" }));
}

async function act(path, body) {
  try { const d = await api("/admin/investigations/" + current.id + path, { method: "POST", body: body || {} }); render(d); }
  catch (e) { alert(e.message); }
}

boot();
