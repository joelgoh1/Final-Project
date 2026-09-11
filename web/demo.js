/* Demo scenarios: fixed fixtures and generated presets in one list, one Analyze
   button, optional fine-tuning. Loaded after app.js and reuses its globals
   ($, analyze, escapeHtml, state). */

const demo = { meta: null, cards: [], scenarios: [], seed: 0, lastRequest: null };

document.addEventListener("DOMContentLoaded", () => {
  document.addEventListener("bootstrap-ready", (event) => initDemoPanel(event.detail));
  $("reshuffle-btn").addEventListener("click", reshuffleDemo);
});

function initDemoPanel(boot) {
  demo.meta = boot.demo;
  demo.cards = boot.cards;
  demo.scenarios = boot.fixtures
    .map((f) => ({ id: `fixture:${f.id}`, kind: "fixture", label: f.label, description: f.description, fixture_id: f.id }))
    .concat(
      demo.meta.presets.map((p) => ({
        id: `preset:${p.id}`,
        kind: "generated",
        label: p.label,
        description: p.description,
        preset_id: p.id,
        config: Object.assign({}, demo.meta.defaults, p.config),
      }))
    );

  $("scenario-list").innerHTML = demo.scenarios
    .map(
      (s, index) => `
      <label class="scenario">
        <input type="radio" name="scenario" value="${escapeHtml(s.id)}" ${index === 0 ? "checked" : ""} />
        <div>
          <h4>${escapeHtml(s.label)} <span class="tag">${s.kind === "fixture" ? "fixed" : "generated"}</span></h4>
          <p>${escapeHtml(s.description)}</p>
        </div>
      </label>`
    )
    .join("");

  $("tune-wallet").innerHTML = demo.cards
    .map(
      (card) =>
        `<label class="held"><input type="checkbox" class="tune-card" value="${escapeHtml(card.id)}" /> ${escapeHtml(card.name)}</label>`
    )
    .join("");

  $("tune-mix").innerHTML = demo.meta.categories
    .map(
      (cat) => `
      <label class="mix-row">
        <span>${escapeHtml(cat.label)}</span>
        <input type="range" min="0" max="100" step="5" class="tune-mix" data-key="${escapeHtml(cat.key)}" />
        <output></output>
      </label>`
    )
    .join("");

  const limits = demo.meta.limits;
  $("tune-count").min = limits.transaction_count.min;
  $("tune-count").max = limits.transaction_count.max;
  $("tune-spend").min = limits.total_spend.min;
  $("tune-spend").max = limits.total_spend.max;
  $("tune-unmapped").min = limits.unmapped_pct.min;
  $("tune-unmapped").max = limits.unmapped_pct.max;

  $("scenario-list").addEventListener("change", () => loadScenario(selectedScenario()));
  $("tune").addEventListener("input", refreshTune);
  $("tune").addEventListener("change", () => {
    syncHabitOptions();
    refreshTune();
  });
  $("tune-shuffle").addEventListener("click", (event) => {
    event.preventDefault();
    demo.seed = Math.floor(Math.random() * 1_000_000);
    refreshTune();
  });
  $("demo-run").addEventListener("click", runScenario);

  loadScenario(demo.scenarios[0]);
}

/* Re-apply persona wording to the controls demo.js owns. Called by toggleMum in
   app.js, since these labels are built in JS rather than carried by data-copy. */
function renderScenarioCopy() {
  if (!demo.scenarios.length) return;
  const scenario = selectedScenario();
  const fixed = scenario.kind === "fixture";
  $("demo-run").textContent = CopyText.t(fixed ? "demo.run.fixed" : "demo.run.generated");
  if (!fixed) {
    syncHabitOptions($("tune-habit").value);
    refreshTune();
  }
}

function selectedScenario() {
  const picked = document.querySelector('input[name="scenario"]:checked');
  return demo.scenarios.find((s) => s.id === (picked ? picked.value : "")) || demo.scenarios[0];
}

/* Fill the fine-tune controls from a scenario. Fixed fixtures cannot be tuned. */
function loadScenario(scenario) {
  const fixed = scenario.kind === "fixture";
  $("tune").classList.toggle("dimmed", fixed);
  $("tune").querySelectorAll("input, select").forEach((el) => (el.disabled = fixed));
  $("tune-fixed-note").hidden = !fixed;
  $("demo-run").textContent = CopyText.t(fixed ? "demo.run.fixed" : "demo.run.generated");
  if (fixed) {
    $("tune-summary").textContent = "";
    return;
  }
  const config = scenario.config;
  document.querySelectorAll(".tune-card").forEach((box) => (box.checked = config.wallet.includes(box.value)));
  $("tune-count").value = config.transaction_count;
  $("tune-spend").value = config.total_spend;
  $("tune-unmapped").value = config.unmapped_pct;
  document.querySelectorAll(".tune-mix").forEach((slider) => (slider.value = config.mix[slider.dataset.key] || 0));
  demo.seed = config.seed;
  syncHabitOptions(habitValue(config.card_mode, config.primary_card));
  refreshTune();
}

function habitValue(mode, primary) {
  return mode === "primary" || mode === "habit" ? `${mode}:${primary || ""}` : mode;
}

function selectedWallet() {
  return Array.from(document.querySelectorAll(".tune-card:checked")).map((box) => box.value);
}

/* One dropdown covers card_mode + primary_card: "Everything on X", "Mostly X", spread, best-rate. */
function syncHabitOptions(preferred) {
  const select = $("tune-habit");
  const current = preferred || select.value;
  const wallet = selectedWallet();
  const name = (id) => (demo.cards.find((c) => c.id === id) || { name: id }).name;
  const options = [
    ["spread", CopyText.t("habit.spread")],
    ["best_rate", CopyText.t("habit.bestRate")],
  ];
  wallet.forEach((id) => options.push([`primary:${id}`, CopyText.fmt("habit.primary", name(id))]));
  wallet.forEach((id) => options.push([`habit:${id}`, CopyText.fmt("habit.mostly", name(id))]));
  select.innerHTML = options
    .map(([value, label]) => `<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`)
    .join("");
  const [mode, primary] = String(current).split(":");
  if (!primary || wallet.includes(primary)) select.value = current;
  if (!select.value) select.value = "spread";
  void mode;
}

function readConfig(scenario) {
  const mix = {};
  document.querySelectorAll(".tune-mix").forEach((slider) => (mix[slider.dataset.key] = Number(slider.value)));
  const [card_mode, primary] = $("tune-habit").value.split(":");
  return {
    preset: scenario.preset_id,
    wallet: selectedWallet(),
    transaction_count: Number($("tune-count").value),
    total_spend: Number($("tune-spend").value),
    mix,
    unmapped_pct: Number($("tune-unmapped").value),
    card_mode,
    primary_card: primary || null,
    seed: demo.seed,
  };
}

function refreshTune() {
  const scenario = selectedScenario();
  if (scenario.kind === "fixture") return;
  const config = readConfig(scenario);
  $("tune-count-out").textContent = `${config.transaction_count}`;
  $("tune-unmapped-out").textContent = `${config.unmapped_pct}%`;
  $("tune-seed").textContent = `seed ${config.seed}`;

  const total = Object.values(config.mix).reduce((a, b) => a + b, 0);
  document.querySelectorAll(".mix-row").forEach((row) => {
    const slider = row.querySelector("input");
    const share = total ? (Number(slider.value) / total) * (100 - config.unmapped_pct) : 0;
    row.querySelector("output").textContent = `${share.toFixed(0)}%`;
    row.classList.toggle("zero", Number(slider.value) === 0);
  });

  const problems = [];
  if (!config.wallet.length) problems.push(CopyText.t("error.pickCard"));
  if (total <= 0) problems.push(CopyText.t("error.pickWeight"));
  $("demo-run").disabled = problems.length > 0;
  $("tune-problems").textContent = problems.join(" ");

  const names = config.wallet.map((id) => (demo.cards.find((c) => c.id === id) || { name: id }).name);
  $("tune-summary").textContent = CopyText.fmt("tune.summaryLine", config, names);
}

async function runScenario() {
  const scenario = selectedScenario();
  if (scenario.kind === "fixture") {
    demo.lastRequest = null;
    await analyze($("demo-run"), () =>
      fetch("/api/analyze/fixture", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fixture_id: scenario.fixture_id }),
      })
    );
    return;
  }
  await postDemo(readConfig(scenario), $("demo-run"));
}

async function reshuffleDemo() {
  if (!demo.lastRequest) return;
  demo.seed = Math.floor(Math.random() * 1_000_000);
  await postDemo(Object.assign({}, demo.lastRequest, { seed: demo.seed }), $("reshuffle-btn"));
}

async function postDemo(request, button) {
  demo.lastRequest = request;
  await analyze(button, () =>
    fetch("/api/analyze/demo", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    })
  );
}

/* Called by app.js after the dashboard renders. */
function renderDemoControls(data) {
  const isDemo = data.source && data.source.kind === "demo";
  $("reshuffle-btn").hidden = !(isDemo && demo.lastRequest);
  const node = $("demo-config-line");
  if (!isDemo || !data.demo_config || !demo.meta) {
    node.hidden = true;
    node.textContent = "";
    return;
  }
  const c = data.demo_config;
  const labels = {};
  demo.meta.categories.forEach((cat) => (labels[cat.key] = cat.label));
  const total = Object.values(c.mix).reduce((a, b) => a + b, 0) || 1;
  const mix = Object.entries(c.mix)
    .filter(([, v]) => v > 0)
    .sort((a, b) => b[1] - a[1])
    .map(([k, v]) => `${labels[k] || k} ${Math.round((v / total) * (100 - c.unmapped_pct))}%`)
    .join(" · ");
  const name = (id) => (demo.cards.find((x) => x.id === id) || { name: id }).name;
  const habit = {
    spread: "spread across the wallet",
    best_rate: "best-rate card per purchase",
    primary: `everything on ${name(c.primary_card)}`,
    habit: `mostly ${name(c.primary_card)}`,
  }[c.card_mode];
  node.hidden = false;
  node.textContent = `Generated: ${mix} · ${c.unmapped_pct}% unfamiliar merchants · ${habit} · seed ${c.seed}`;
}
