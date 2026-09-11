/* Strategist extras: reasoning tree + talk-back chat.
   Loaded after app.js; wraps renderAdvisor so app.js itself stays untouched. */

(function () {
  const STYLE = `
    .strat-extras { margin-top: .9rem; border-top: 1px solid var(--line); padding-top: .8rem; }
    .strat-extras details { margin-bottom: .6rem; }
    .strat-extras summary { cursor: pointer; font-size: .82rem; font-weight: 600; }
    .tree { list-style: none; margin: .5rem 0 0; padding: 0; font-size: .78rem; }
    .tree > li { border-left: 2px solid var(--line); margin-left: .3rem; padding: .25rem 0 .25rem .7rem; }
    .tree .turn-head { display: flex; gap: .5rem; align-items: baseline; flex-wrap: wrap; }
    .tree .turn-head strong { font-size: .8rem; }
    .tree .tok { color: var(--muted); font-size: .72rem; }
    .tree .thinking { color: var(--muted); white-space: pre-wrap; margin: .25rem 0; max-height: 9rem; overflow: auto; font-size: .74rem; }
    .tree ul { list-style: none; margin: .25rem 0 0; padding-left: .6rem; }
    .tree ul li { padding: .12rem 0; }
    .tree code { background: var(--track); border-radius: 4px; padding: 0 .3rem; font-size: .72rem; }
    .tree .good { color: var(--good); font-weight: 600; }
    .tree .bad { color: var(--warn); font-weight: 600; }
    .tree .answer { color: var(--ink); white-space: pre-wrap; }
    .baseline { font-size: .78rem; color: var(--muted); margin-top: .3rem; }
    .chat-log { display: grid; gap: .4rem; margin: .5rem 0; max-height: 18rem; overflow: auto; }
    .bubble { padding: .45rem .65rem; border-radius: 10px; font-size: .82rem; white-space: pre-wrap; max-width: 92%; }
    .bubble.user { background: var(--brand-soft); color: var(--ink); justify-self: end; }
    .bubble.assistant { background: var(--surface-2); border: 1px solid var(--line); justify-self: start; }
    .bubble.meta { background: transparent; color: var(--muted); font-size: .74rem; justify-self: center; }
    .chat-row { display: flex; gap: .4rem; }
    .chat-row input { flex: 1; padding: .5rem .65rem; border-radius: 9px; border: 1px solid var(--line); background: var(--surface-2); color: var(--ink); font-size: .84rem; }
    .chat-row input:disabled { opacity: .6; }
    .chips { display: flex; flex-wrap: wrap; gap: .35rem; margin: .4rem 0 .2rem; }
    .chips button { border: 1px solid var(--line); background: var(--surface-2); color: var(--muted); border-radius: 999px; padding: .2rem .6rem; font-size: .74rem; cursor: pointer; }
    .chips button:hover { border-color: var(--brand); color: var(--brand); }
  `;
  const style = document.createElement("style");
  style.textContent = STYLE;
  document.head.appendChild(style);

  const PROMPTS = [
    "Why not put dining on the 6% card?",
    "I don't want to use OCBC at all.",
    "Give me a 2-rule version.",
    "Which rule is most likely to break?",
  ];

  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const money = (n) => "$" + Number(n || 0).toFixed(2);
  const chat = { history: [], sessionId: null, busy: false };

  function currentSessionId() {
    return (window.state && state.session && state.session.session_id) || null;
  }

  function renderTree(advisor) {
    const trace = advisor.trace || [];
    if (!trace.length) return "";
    const turns = trace
      .map((t) => {
        const tok = t.tokens || {};
        const calls = (t.tool_calls || [])
          .map((c) => {
            const r = c.result || {};
            let verdict;
            if (r.error) verdict = `<span class="bad">error: ${esc(r.error)}</span>`;
            else if (typeof r.total_reward === "number")
              verdict = `<span class="good">${money(r.total_reward)}</span> <span class="tok">(${(r.vs_actual >= 0 ? "+" : "") + Number(r.vs_actual || 0).toFixed(2)} vs actual)</span>`;
            else verdict = `<span class="tok">${esc(Object.keys(r).join(", "))}</span>`;
            const rules = r.strategy && r.strategy.rules ? Object.entries(r.strategy.rules).map(([k, v]) => `${k}→${v}`).join(", ") : "";
            const moves = (r.moves || []).map((m) => `<li class="tok">↳ ${esc(m)}</li>`).join("");
            return `<li><code>${esc(c.name)}</code> ${rules ? `<span class="tok">${esc(rules)}${r.strategy.default_card_id ? `, else→${esc(r.strategy.default_card_id)}` : ""}</span> ` : ""}→ ${verdict}${moves ? `<ul>${moves}</ul>` : ""}</li>`;
          })
          .join("");
        const thinking = (t.reasoning || "").trim();
        const answer = !t.tool_calls || !t.tool_calls.length ? (t.text || "").trim() : "";
        return `<li>
          <div class="turn-head"><strong>Turn ${t.turn}</strong>
            <span class="tok">${t.tools_offered ? "tools offered" : "answer only"} · reasoning ${tok.reasoning ?? "?"} tok · output ${tok.completion ?? "?"} tok</span></div>
          ${thinking ? `<div class="thinking">${esc(thinking)}${t.reasoning_truncated ? " …" : ""}</div>` : `<div class="tok">(provider returned no reasoning text)</div>`}
          ${calls ? `<ul>${calls}</ul>` : ""}
          ${answer ? `<div class="answer">${esc(answer.length > 600 ? answer.slice(0, 600) + " …" : answer)}</div>` : ""}
        </li>`;
      })
      .join("");
    const base = advisor.best_engine_plan
      ? `<p class="baseline">Engine baseline it was judged against: <strong>${esc(advisor.best_engine_plan.label)}</strong> = ${money(advisor.best_engine_plan.total_reward)}</p>`
      : "";
    return `<details><summary>Reasoning tree · ${trace.length} turn${trace.length === 1 ? "" : "s"}, ${(advisor.trace || []).reduce((n, t) => n + (t.tool_calls || []).length, 0)} tool calls</summary><ul class="tree">${turns}</ul>${base}</details>`;
  }

  function renderChat() {
    const log = chat.history
      .map((m) => `<div class="bubble ${m.role === "user" ? "user" : "assistant"}">${esc(m.text)}</div>`)
      .join("");
    return `<details open><summary>Talk to the strategist</summary>
      <div class="chips">${PROMPTS.map((p) => `<button type="button" data-prompt="${esc(p)}">${esc(p)}</button>`).join("")}</div>
      <div class="chat-log" id="chat-log">${log || '<div class="bubble meta">Push back, complain, or ask why. If it changes its mind, the plan above updates - engine-verified.</div>'}</div>
      <form class="chat-row" id="chat-form">
        <input id="chat-input" type="text" maxlength="2000" placeholder="e.g. I never carry the OCBC card - re-plan without it" autocomplete="off" />
        <button class="btn secondary" type="submit" id="chat-send">Send</button>
      </form></details>`;
  }

  function mount(advisor) {
    const body = document.getElementById("advisor-body");
    if (!body || advisor.mode !== "agent") return;
    const sid = currentSessionId();
    if (sid !== chat.sessionId) {
      chat.sessionId = sid;
      chat.history = [];
    }
    let extras = document.getElementById("strat-extras");
    if (!extras) {
      extras = document.createElement("div");
      extras.id = "strat-extras";
      extras.className = "strat-extras";
    }
    extras.innerHTML = renderTree(advisor) + renderChat();
    body.appendChild(extras);
    extras.querySelector("#chat-form").addEventListener("submit", onSend);
    extras.querySelectorAll(".chips button").forEach((b) => b.addEventListener("click", () => send(b.dataset.prompt)));
    const log = extras.querySelector("#chat-log");
    log.scrollTop = log.scrollHeight;
  }

  async function onSend(event) {
    event.preventDefault();
    const input = document.getElementById("chat-input");
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    await send(text);
  }

  async function send(text) {
    if (chat.busy || !chat.sessionId) return;
    chat.busy = true;
    const log = document.getElementById("chat-log");
    log.insertAdjacentHTML("beforeend", `<div class="bubble user">${esc(text)}</div><div class="bubble meta" id="chat-wait">strategist is checking the engine…</div>`);
    log.scrollTop = log.scrollHeight;
    document.getElementById("chat-send").disabled = true;
    document.getElementById("chat-input").disabled = true;
    try {
      const response = await fetch(`/api/advisor/${chat.sessionId}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "The strategist could not answer.");
      if (data.mode !== "agent") throw new Error(data.detail || data.reply || "Unavailable.");
      chat.history = data.history || chat.history.concat([{ role: "user", text }, { role: "assistant", text: data.reply }]);
      if (data.plan) {
        // The model changed its mind: re-render the plan (engine-verified) and keep the chat.
        renderAdvisor(data.plan);
        const l = document.getElementById("chat-log");
        l.insertAdjacentHTML("beforeend", `<div class="bubble meta">plan updated · now ${money(data.plan.verified_rewards)} engine-verified</div>`);
        l.scrollTop = l.scrollHeight;
      } else {
        const wait = document.getElementById("chat-wait");
        if (wait) wait.remove();
        log.insertAdjacentHTML("beforeend", `<div class="bubble assistant">${esc(data.reply)}</div>`);
        log.scrollTop = log.scrollHeight;
      }
    } catch (error) {
      const wait = document.getElementById("chat-wait");
      if (wait) wait.remove();
      const l = document.getElementById("chat-log");
      if (l) l.insertAdjacentHTML("beforeend", `<div class="bubble meta">${esc(error.message)}</div>`);
    } finally {
      chat.busy = false;
      const sendBtn = document.getElementById("chat-send");
      const inp = document.getElementById("chat-input");
      if (sendBtn) sendBtn.disabled = false;
      if (inp) { inp.disabled = false; inp.focus(); }
    }
  }

  if (typeof renderAdvisor === "function") {
    const original = renderAdvisor;
    // Classic-script function declarations are writable globals, so app.js needs no edit.
    renderAdvisor = function (advisor) {
      original(advisor);
      mount(advisor);
    };
  }
  window.Strategist = { mount, send };
})();
