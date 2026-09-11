/* Card Reward & Spend Optimizer - single screen, no framework, no build step. */

const $ = (id) => document.getElementById(id);
const money = (n) => "$" + Number(n || 0).toFixed(2);
const state = { session: null, months: [], totals: null, advisorStatus: null };
const t = (key) => window.CopyText.t(key);
const fmt = (key, ...args) => window.CopyText.fmt(key, ...args);
// One-shot guard: renderDashboard also runs on month switches and re-renders,
// and the money rain is a reveal, not a redraw.
let lastReveal = null;

document.addEventListener("DOMContentLoaded", init);

async function init() {
  $("upload-btn").addEventListener("click", runUpload);
  $("reset-btn").addEventListener("click", removeActiveMonth);
  $("rename-btn").addEventListener("click", renameActiveMonth);
  $("add-month-btn").addEventListener("click", showStart);
  $("clear-all-btn").addEventListener("click", clearAllMonths);
  $("months-return-btn").addEventListener("click", () => state.session && showDashboard());
  $("theme-toggle").addEventListener("click", toggleTheme);
  $("mum-toggle").addEventListener("click", toggleMum);
  syncThemeButton();
  window.CopyText.applyCopy();
  syncMumButton();
  document.addEventListener("fx-ready", () => {
    if (window.fx) window.fx.setMoneyCursor(currentMode() === "mum");
  });
  if (window.fx) window.fx.setMoneyCursor(currentMode() === "mum");

  const boot = await fetch("/api/bootstrap").then((r) => r.json());
  state.advisorStatus = boot.advisor_status;

  const select = $("card-override");
  boot.cards.forEach((card) => {
    const option = document.createElement("option");
    option.value = card.id;
    option.textContent = `${card.name} (${card.issuer})`;
    select.appendChild(option);
  });

  $("held-cards").innerHTML = boot.cards
    .map(
      (card) =>
        `<label class="held"><input type="checkbox" class="held-card" value="${escapeHtml(card.id)}" /> ${escapeHtml(card.name)}</label>`
    )
    .join("");

  $("wallet-preview").innerHTML = boot.cards
    .map((card) => {
      const rates = Object.entries(card.headline_rates)
        .map(([label, rate]) => `${label} ${rate}`)
        .join(", ") || `flat ${card.base_rate_label}`;
      return `<span class="chip"><strong>${escapeHtml(card.name)}</strong> &middot; ${escapeHtml(rates)}</span>`;
    })
    .join("");

  if (boot.samples.length) {
    $("sample-hint").textContent =
      "Sample unlocked PDFs for testing live in the project's samples/ folder: " + boot.samples.join(", ");
  }

  const toggle = $("advisor-enabled");
  if (!boot.advisor_status.available) {
    toggle.checked = false;
    toggle.disabled = true;
    $("advisor-note").textContent = boot.advisor_status.reason +
      " The dashboard below still works - the rules engine runs entirely on your machine.";
  } else {
    $("advisor-note").textContent =
      `Model ${boot.advisor_status.model} via ${boot.advisor_status.provider}. Only redacted rows ` +
      "(date, merchant, amount) are sent to the model - never names, addresses or card numbers. Untick to stay fully local.";
  }

  // Scenario list and fine-tune panel live in demo.js and build off the same payload.
  document.dispatchEvent(new CustomEvent("bootstrap-ready", { detail: boot }));

  // Months already held by the server survive a page reload.
  await refreshMonths();
  if (state.months.length) await showMonth(state.months[state.months.length - 1].session_id);
}

async function runUpload() {
  const files = $("file-input").files;
  if (!files || !files.length) {
    showError(t("error.noFile"));
    return;
  }
  const form = new FormData();
  Array.from(files).forEach((file) => form.append("files", file));
  const cardId = $("card-override").value;
  if (cardId) form.append("card_id", cardId);
  const held = Array.from(document.querySelectorAll(".held-card:checked")).map((c) => c.value);
  if (held.length) form.append("wallet", held.join(","));
  const label = $("upload-label").value.trim();
  if (label) form.append("label", label);
  await analyze($("upload-btn"), () => fetch("/api/analyze/upload", { method: "POST", body: form }));
}

async function analyze(button, request) {
  showError(null);
  const label = button.textContent;
  button.disabled = true;
  button.textContent = t("status.analyzing");
  try {
    const response = await request();
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Analysis failed.");
    state.session = data;
    $("file-input").value = "";
    $("upload-label").value = "";
    await refreshMonths();
    renderDashboard(data);
    if ($("advisor-enabled").checked) runAdvisor(data.session_id);
    else renderAdvisorOff();
  } catch (error) {
    showError(error.message);
  } finally {
    button.disabled = false;
    button.textContent = label;
  }
}

/* ---------- Months: list / switch / rename / remove ---------- */

async function refreshMonths() {
  try {
    const body = await fetch("/api/months").then((r) => r.json());
    state.months = body.months;
    state.totals = body.totals;
  } catch (error) {
    state.months = [];
    state.totals = null;
  }
  renderMonthTabs();
  renderOverview();
  renderMonthsReturn();
}

function activeMonthId() {
  return state.session ? state.session.session_id : null;
}

function renderMonthTabs() {
  const tabs = $("month-tabs");
  tabs.innerHTML = "";
  state.months.forEach((month) => {
    const tab = document.createElement("button");
    tab.type = "button";
    tab.className = "month-tab" + (month.session_id === activeMonthId() ? " active" : "");
    tab.title = `${month.source.label || ""} · ${month.summary.transaction_count} transactions`;
    tab.innerHTML = `<span>${escapeHtml(month.label)}</span><span class="missed">${money(month.summary.missed_value)} missed</span>`;
    tab.addEventListener("click", () => showMonth(month.session_id));
    tabs.appendChild(tab);
  });
  $("clear-all-btn").hidden = state.months.length < 2;
}

function renderOverview() {
  const months = state.months;
  const panel = $("overview");
  if (months.length < 2 || !state.totals) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  $("overview-count").textContent = `${months.length} months`;
  const body = $("overview-table").querySelector("tbody");
  body.innerHTML = months
    .map((m) => {
      const s = m.summary;
      return `
      <tr data-id="${escapeHtml(m.session_id)}" class="${m.session_id === activeMonthId() ? "active" : ""}">
        <td>${escapeHtml(m.label)}</td>
        <td class="muted">${escapeHtml(m.source.kind === "upload" ? m.source.label || "upload" : "demo")}</td>
        <td class="num">${money(s.total_spend)}</td>
        <td class="num">${money(s.actual_rewards)}</td>
        <td class="num">${money(s.optimal_rewards)}</td>
        <td class="num missed">${money(s.missed_value)}</td>
        <td class="num">${s.actual_yield_pct}% &rarr; ${s.optimal_yield_pct}%</td>
      </tr>`;
    })
    .join("");
  body.querySelectorAll("tr").forEach((row) => row.addEventListener("click", () => showMonth(row.dataset.id)));
  // Named `totals`, not `t`: `t` is the copy-lookup helper at the top of this file.
  const totals = state.totals;
  $("overview-table").querySelector("tfoot").innerHTML = `
    <tr>
      <td>All months</td>
      <td class="muted">${totals.transaction_count} transactions</td>
      <td class="num">${money(totals.total_spend)}</td>
      <td class="num">${money(totals.actual_rewards)}</td>
      <td class="num">${money(totals.optimal_rewards)}</td>
      <td class="num missed">${money(totals.missed_value)}</td>
      <td class="num">${totals.actual_yield_pct}% &rarr; ${totals.optimal_yield_pct}%</td>
    </tr>`;
}

function renderMonthsReturn() {
  const n = state.months.length;
  $("months-return").hidden = !n;
  $("start-title").textContent = t(n ? "start.titleMore" : "start.title");
  if (n) {
    $("months-return-text").textContent =
      `${n} month${n === 1 ? "" : "s"} held in memory: ${state.months.map((m) => m.label).join(", ")}.`;
  }
}

async function showMonth(sessionId) {
  showError(null);
  try {
    const response = await fetch(`/api/months/${sessionId}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "That month is no longer available.");
    state.session = data;
    renderDashboard(data);
    if (data.advisor) renderAdvisor(data.advisor);
    else renderAdvisorNotRun(sessionId);
  } catch (error) {
    await refreshMonths();
    if (!state.months.length) {
      state.session = null;
      showStart();
    }
    showError(error.message);
  }
}

function showStart() {
  $("dashboard").hidden = true;
  $("start").hidden = false;
  renderMonthsReturn();
  window.scrollTo({ top: 0 });
}

function showDashboard() {
  $("start").hidden = true;
  $("dashboard").hidden = false;
  window.scrollTo({ top: 0 });
}

async function renameActiveMonth() {
  if (!state.session) return;
  const current = state.session.label || "";
  const next = window.prompt(t("prompt.rename"), current);
  if (next == null || !next.trim() || next.trim() === current) return;
  const response = await fetch(`/api/months/${state.session.session_id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ label: next.trim() }),
  });
  const body = await response.json();
  if (!response.ok) {
    showError(body.detail || "Could not rename that month.");
    return;
  }
  state.session.label = body.label;
  await refreshMonths();
  renderSourceLine(state.session);
}

async function removeActiveMonth() {
  if (!state.session) return;
  if (!window.confirm(fmt("confirm.removeMonth", state.session.label))) return;
  const removedId = state.session.session_id;
  await fetch(`/api/months/${removedId}`, { method: "DELETE" }).catch(() => {});
  state.session = null;
  await refreshMonths();
  const remaining = state.months.filter((m) => m.session_id !== removedId);
  if (remaining.length) await showMonth(remaining[remaining.length - 1].session_id);
  else showStart();
}

async function clearAllMonths() {
  if (!window.confirm(fmt("confirm.clearAll", state.months.length))) return;
  await fetch("/api/months", { method: "DELETE" }).catch(() => {});
  state.session = null;
  await refreshMonths();
  showStart();
}

function renderSourceLine(data) {
  const s = data.summary;
  $("source-label").textContent = data.label || data.source.label || "Statement analysis";
  $("source-meta").textContent = [
    data.source.label && data.source.label !== data.label ? data.source.label : null,
    data.source.cycle_label && data.source.cycle_label !== data.label ? data.source.cycle_label : null,
    `${s.transaction_count} transactions`,
    data.source.kind === "upload" ? "your upload, held in memory only" : "synthetic demo data",
  ]
    .filter(Boolean)
    .join(" · ");
}

/* ---------- Dashboard for the active month ---------- */

function renderDashboard(data) {
  const s = data.summary;
  renderSourceLine(data);
  renderMonthTabs();
  renderOverview();

  $("kpi-missed").textContent = money(s.missed_value);
  $("kpi-actual").textContent = money(s.actual_rewards);
  $("kpi-optimal").textContent = money(s.optimal_rewards);
  $("verdict").classList.toggle("clean", !!s.already_optimal);
  $("verdict-lede").innerHTML = fmt("verdict.lede", s, money);
  $("kpi-missed-sub").textContent = fmt("verdict.sub", data);
  // both bars are shares of the optimal figure, so they are directly comparable
  const earnedShare = s.optimal_rewards > 0 ? (s.actual_rewards / s.optimal_rewards) * 100 : 0;
  $("bar-actual").style.width = `${Math.min(earnedShare, 100)}%`;
  $("bar-optimal").style.width = "100%";

  // Mum mode: your money, visibly flying away. Once per reveal - a new month or
  // a fresh toggle - never on a rename, an advisor update or a repeat tab click.
  const revealKey = `${data.session_id}:${currentMode()}`;
  if (currentMode() === "mum" && revealKey !== lastReveal) {
    lastReveal = revealKey;
    if (window.fx) {
      const box = $("kpi-missed").getBoundingClientRect();
      const x = box.left + box.width / 2;
      if (s.missed_value > 0) {
        window.fx.countUp($("kpi-missed"), s.missed_value, { format: money });
        window.fx.coinBurst(x, box.bottom - 8, 14, 1);
        window.fx.moneyRain(1800, 1.1);
      } else {
        window.fx.confetti(x, box.top); // nothing to scold for
      }
    }
  }

  const maxSpend = Math.max(...data.categories.map((c) => c.spend), 1);
  $("category-bars").innerHTML = data.categories
    .map(
      (c) => `
      <div class="bar-row ${c.key === "general_spend" ? "guardrail" : ""}">
        <div class="bar-head">
          <span>${escapeHtml(c.label)}${c.key === "general_spend" ? " (guardrail)" : ""}</span>
          <span>${money(c.spend)} &middot; ${c.share_pct}%</span>
        </div>
        <div class="bar-track"><div class="bar-fill" style="width:${(c.spend / maxSpend) * 100}%"></div></div>
      </div>`
    )
    .join("");

  $("wallet-rules").innerHTML = data.wallet_rules
    .map(
      (rule) => `
      <div class="rule">
        <h4>
          <span>${escapeHtml(rule.category_label)}</span>
          <svg class="arrow" width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor"
               stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path
               d="M4 12h15" /><path d="M13 6l6 6-6 6" /></svg>
          <span class="to">${escapeHtml(rule.card_name)}</span>
        </h4>
        <span class="amount">${money(rule.projected_reward)}</span>
        <p class="rate">${escapeHtml(fmt("rule.next", rule))}</p>
        <p class="cond">${escapeHtml(rule.condition)}</p>
      </div>`
    )
    .join("");

  $("suboptimal-count").textContent = `${data.suboptimal_count} found`;
  const rows = data.suboptimal
    .map(
      (r) => `
      <tr>
        <td>${escapeHtml(r.date)}</td>
        <td>${escapeHtml(r.merchant)}</td>
        <td>${escapeHtml(r.category_label)}</td>
        <td class="num">${money(r.amount)}</td>
        <td>${escapeHtml(r.actual_card)}</td>
        <td>${escapeHtml(r.better_card)}</td>
        <td class="num missed">${money(r.missed)}</td>
      </tr>`
    )
    .join("");
  $("suboptimal-table").querySelector("tbody").innerHTML =
    rows || `<tr><td colspan="7">${escapeHtml(t("table.allOptimal"))}</td></tr>`;

  $("cards-table").querySelector("tbody").innerHTML = data.cards
    .map(
      (c) => `
      <tr>
        <td>${escapeHtml(c.name)}</td>
        <td class="num">${money(c.actual_spend)}</td>
        <td class="num">${money(c.actual_reward)}</td>
        <td>${c.min_spend ? `${money(c.min_spend)} <span class="flag ${c.min_spend_met ? "ok" : "bad"}">${escapeHtml(t(c.min_spend_met ? "flag.minMet" : "flag.minMissed"))}</span>` : "none"}</td>
        <td>${c.monthly_cap ? `${money(c.monthly_cap)} <span class="flag ${c.cap_reached ? "bad" : "ok"}">${escapeHtml(t(c.cap_reached ? "flag.capHit" : "flag.capHeadroom"))}</span>` : "none"}</td>
        <td class="num">${money(c.optimal_reward)}</td>
      </tr>`
    )
    .join("");

  const q = data.parse_quality;
  $("parse-quality").textContent = `${q.rows_parsed} rows · ${q.general_spend_rows} guardrailed · ${q.lines_skipped} skipped`;
  $("assumptions").innerHTML = q.notes
    .concat(data.assumptions)
    .map((note) => `<li>${escapeHtml(note)}</li>`)
    .join("");

  $("csv-link").href = `/api/export/${data.session_id}.csv`;
  if (typeof renderDemoControls === "function") renderDemoControls(data);
  $("start").hidden = true;
  $("dashboard").hidden = false;
  window.scrollTo({ top: 0 });
}

function renderAdvisorNotRun(sessionId) {
  if (!(state.advisorStatus && state.advisorStatus.available)) {
    renderAdvisorOff();
    return;
  }
  $("advisor-body").innerHTML = `
    <p class="muted small">${escapeHtml(t("advisor.notRun"))}</p>
    <button id="run-advisor-btn" class="btn secondary" type="button">${escapeHtml(t("advisor.runBtn"))}</button>
    <p class="muted small">Sends only redacted rows (date, merchant, amount) to the model.</p>`;
  $("run-advisor-btn").addEventListener("click", () => runAdvisor(sessionId));
}

function renderAdvisorOff() {
  $("advisor-body").innerHTML = `<p class="muted small">${
    state.advisorStatus && state.advisorStatus.available
      ? "Turned off for this run - the cheat sheet below is from the local rules engine."
      : escapeHtml((state.advisorStatus && state.advisorStatus.reason) || "Unavailable.")
  }</p>`;
}

async function runAdvisor(sessionId) {
  $("advisor-body").innerHTML = `
    <div class="thinking"><div class="spinner"></div>
    <span>${escapeHtml(t("advisor.thinking"))}</span></div>`;
  try {
    const response = await fetch(`/api/advisor/${sessionId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ persona: window.CopyText.currentPersona() }),
    });
    const advisor = await response.json();
    if (!response.ok) throw new Error(advisor.detail || "The strategist could not be reached.");
    if (state.session && state.session.session_id === sessionId) {
      state.session.advisor = advisor;
      renderAdvisor(advisor);
    }
  } catch (error) {
    $("advisor-body").innerHTML = `<p class="error">${escapeHtml(error.message)}</p>
      <p class="muted small">The rules-engine cheat sheet below is unaffected.</p>`;
  }
}

function renderAdvisor(advisor) {
  const header = document.querySelector("#strategist-card h3");
  header.innerHTML = escapeHtml(t("section.strategist"));
  if (advisor.mode !== "agent") {
    $("advisor-body").innerHTML = `<p class="muted small">${escapeHtml(advisor.detail || "Unavailable.")}</p>`;
    return;
  }
  header.innerHTML =
    `${escapeHtml(t("section.strategist"))} <span class="tag live">${escapeHtml(advisor.model)}</span>`;

  const stale = advisor.persona && advisor.persona !== window.CopyText.currentPersona();
  const staleNote = stale
    ? `<p class="muted small">${escapeHtml(t("advisor.stalePlan"))}
         <button id="advisor-rerun-btn" class="btn ghost" type="button">${escapeHtml(t("advisor.rerunBtn"))}</button></p>`
    : "";

  const rules = advisor.rules
    .map(
      (rule) => `
      <div class="rule">
        <h4>${escapeHtml(rule.category_label)} &rarr; ${escapeHtml(rule.card_name)}</h4>
        <p>${escapeHtml(rule.rationale)}</p>
      </div>`
    )
    .join("");

  const watchOuts = advisor.watch_outs.length
    ? `<ul class="disclosure-list muted small">${advisor.watch_outs.map((w) => `<li>${escapeHtml(w)}</li>`).join("")}</ul>`
    : "";

  $("advisor-body").innerHTML = `
    ${staleNote}
    <p><strong>${escapeHtml(advisor.headline)}</strong></p>
    <div class="rules">${rules}</div>
    <p class="muted small">Everything else &rarr; ${escapeHtml(advisor.default_card_name)}</p>
    <p class="muted small">${escapeHtml(advisor.reasoning_summary)}</p>
    ${watchOuts}
    <p class="muted small">
      Projected next cycle on this plan: <strong>${money(advisor.verified_rewards)}</strong>
      &mdash; re-scored by the local rules engine, not by the model
      (${advisor.strategies_tested} candidate strategies tested, ${escapeHtml(advisor.path)} mode).
    </p>
    ${advisor.detail ? `<p class="muted small">${escapeHtml(advisor.detail)}</p>` : ""}`;
  const rerun = $("advisor-rerun-btn");
  if (rerun && state.session) {
    rerun.addEventListener("click", () => runAdvisor(state.session.session_id));
  }
}

function currentTheme() {
  return document.documentElement.getAttribute("data-theme") || "light";
}

function toggleTheme() {
  const next = currentTheme() === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  localStorage.setItem("theme", next);
  syncThemeButton();
}

function syncThemeButton() {
  $("theme-toggle").textContent = currentTheme() === "dark" ? "Light mode" : "Dark mode";
}

/* Naggy mum mode. A separate attribute from data-theme on purpose: the voice
   and the light/dark skin are independent axes, so all four combinations work. */
function currentMode() {
  return document.documentElement.getAttribute("data-mode") || "plain";
}

function toggleMum() {
  const on = currentMode() !== "mum";
  const root = document.documentElement;
  if (on) root.setAttribute("data-mode", "mum");
  else root.removeAttribute("data-mode");
  localStorage.setItem("mode", on ? "mum" : "plain");

  window.CopyText.applyCopy();
  syncMumButton();
  if (window.fx) window.fx.setMoneyCursor(on);
  // Re-render so every interpolated sentence is rebuilt in the new voice.
  if (state.session) {
    renderDashboard(state.session);
    if (state.session.advisor) renderAdvisor(state.session.advisor);
    else renderAdvisorNotRun(state.session.session_id);
  } else {
    renderMonthsReturn();
  }
  if (typeof renderScenarioCopy === "function") renderScenarioCopy();
}

function syncMumButton() {
  const on = currentMode() === "mum";
  $("mum-toggle").textContent = t(on ? "mum.toggle.on" : "mum.toggle.off");
  $("mum-toggle").setAttribute("aria-pressed", String(on));
}

function showError(message) {
  const node = $("start-error");
  node.hidden = !message;
  node.textContent = message || "";
}

function escapeHtml(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, (ch) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch])
  );
}
