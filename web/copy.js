/* copy.js - persona wording. Loaded before app.js; classic script, no exports.

   Two ways in:
     * static nodes carry data-copy="key" and are swept by applyCopy()
     * interpolated sentences call CopyText.fmt("key", ...) from app.js etc.

   Safety rule: data-copy values are written with textContent, so they can
   never inject markup. The few entries that produce markup are
   developer-authored literals built with the h`` tag, which escapes every
   ${} - so engine and model strings stay escaped exactly as before.

   Anything mum mode does not override falls back to the analyst wording, so
   the analyst persona is byte-identical to the app before this feature. */

(function () {
  const esc = (v) =>
    String(v == null ? "" : v).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
    );

  /** Tagged template: literal parts stay raw markup, every ${} is escaped. */
  const h = (parts, ...vals) =>
    parts.reduce((out, part, i) => out + part + (i < vals.length ? esc(vals[i]) : ""), "");

  const cash = (n) => "$" + Number(n || 0).toFixed(2);
  const PLATE = 4.5; // one plate of hawker chicken rice
  const plates = (n) => Math.max(1, Math.round(Number(n || 0) / PLATE));

  const COPY = {
    analyst: {
      /* --- topbar / chrome --- */
      "app.title": "Card Reward & Spend Optimizer",
      "app.tagline": "See the cashback your wallet left on the table last cycle.",
      "privacy.pill": "No bank logins · processed in memory · nothing saved to disk",
      "mum.toggle.off": "Call Mum",
      "mum.toggle.on": "Mum: ON",
      "mum.toggle.aria": "Naggy Singaporean mum mode",

      /* --- start screen --- */
      "start.title": "Start with a demo statement",
      "start.titleMore": "Add another month",
      "start.blurb":
        "One click to a full reward-leakage analysis. No file, no login, no account. " +
        "Each statement you add becomes a month you can switch between and total up.",
      "tune.summary": "Fine-tune",
      "upload.summary": "Or upload your own unlocked PDF e-statement",
      "demo.run.fixed": "Analyze this statement",
      "demo.run.generated": "Generate & analyze",
      "status.analyzing": "Analyzing...",
      "error.noFile": "Choose at least one unlocked PDF e-statement first.",
      "error.pickCard": "Pick at least one card.",
      "error.pickWeight": "Give at least one category some weight.",
      "months.back": "Back to months",

      /* --- verdict --- */
      "verdict.label": "Left on the table this cycle",
      "verdict.note":
        "That upper figure re-routes every purchase perfectly, which nobody manages in " +
        "practice. The three rules below are the version you can actually follow.",
      "verdict.earned": "What you earned",
      "verdict.couldBe": "What it could have been",

      /* --- section headings --- */
      "section.overview": "Across your months",
      "section.rules": "Three rules for next month",
      "section.categories": "Where the money went",
      "section.strategist": "AI strategist — next cycle",
      "section.suboptimal": "Suboptimal transactions",
      "section.cards": "Cards this cycle",
      "section.method": "How these numbers were produced",

      /* --- table headers with persona wording --- */
      "th.chargedTo": "Charged to",
      "th.betterCard": "Better card",
      "th.missed": "Missed",

      /* --- flags --- */
      "flag.minMet": "met",
      "flag.minMissed": "missed",
      "flag.capHit": "cap hit",
      "flag.capHeadroom": "headroom",
      "table.allOptimal": "Every transaction was already on the best available card.",

      /* --- strategist --- */
      "advisor.notRun": "The strategist has not planned from this month yet.",
      "advisor.runBtn": "Run strategist for this month",
      "advisor.stalePlan": "This plan is in the other voice.",
      "advisor.rerunBtn": "Re-run in this voice",
      "chat.title": "Talk to the strategist",
      "chat.empty":
        "Push back, complain, or ask why. If it changes its mind, the plan above updates - engine-verified.",
      "chat.placeholder": "e.g. I never carry the OCBC card - re-plan without it",
      "chat.waiting": "strategist is checking the engine…",
      "chat.send": "Send",
      "chat.chips": [
        "Why not put dining on the 6% card?",
        "I don't want to use OCBC at all.",
        "Give me a 2-rule version.",
        "Which rule is most likely to break?",
      ],

      /* --- prompts / confirms --- */
      "prompt.rename": "Name for this month:",
      "confirm.removeMonth": (label) => `Remove "${label}" from memory?`,
      "confirm.clearAll": (n) => `Clear all ${n} months from memory?`,

      /* --- markup-producing (developer literals; ${} is escaped by h) --- */
      "verdict.lede": (s, money) =>
        h`You earned <strong>${s.actual_yield_pct}%</strong> on ${money(s.total_spend)} of spend ` +
        h`across ${s.transaction_count} line items. The same purchases, on the cards already in ` +
        h`your wallet, would have paid <strong class="pos">${s.optimal_yield_pct}%</strong>.`,

      /* --- plain formatters (textContent) --- */
      "verdict.sub": (data) =>
        data.summary.already_optimal
          ? "Your allocation is already optimal for these rules."
          : `${data.suboptimal_count} of ${data.summary.transaction_count} purchases went on the wrong card.`,
      "rule.next": (rule) => `next month, at ${rule.rate_label}`,
      "tune.summaryLine": (config, names) =>
        `${config.transaction_count} transactions, about $${config.total_spend.toLocaleString()} ` +
        `on ${names.join(", ") || "no cards"}.`,
      "habit.spread": "Spread at random across the wallet",
      "habit.bestRate": "Best-rate card for each purchase",
      "habit.primary": (name) => `Everything on ${name}`,
      "habit.mostly": (name) => `Mostly ${name}, some spillover`,
    },

    mum: {
      /* --- topbar / chrome --- */
      "app.title": "Mummy's Cashback Report Card",
      "app.tagline": "Come, sit down. Let Mummy see how much money you anyhow throw away.",
      "privacy.pill": "No bank login ah · all in memory only · Mummy never keep your things",

      /* --- start screen --- */
      "start.title": "Show Mummy one statement first",
      "start.titleMore": "Got another month? Bring it come",
      "start.blurb":
        "One click only, then Mummy tell you everything. No file, no login, no account. " +
        "Every statement you show becomes one month, then we add up and see the damage.",
      "tune.summary": "Want to change what? Open lah",
      "upload.summary": "Or give Mummy your own PDF statement (must be unlocked one)",
      "demo.run.fixed": "Okay Mummy, see lah",
      "demo.run.generated": "Make one, let Mummy see",
      "status.analyzing": "Wait ah, Mummy counting...",
      "error.noFile": "Aiyo, you never choose any file. Pick one first lah.",
      "error.pickCard": "Choose at least one card lah, how to check like that?",
      "error.pickWeight": "Must put some money somewhere what. Give one category a bit.",
      "months.back": "Go back to the months",

      /* --- verdict --- */
      "verdict.label": "Money you simply throw away this month",
      "verdict.note":
        "That top number is if you never make one single mistake - nobody like that one, " +
        "not even Mummy. The three rules below is the version you can actually remember " +
        "when you standing at the cashier.",
      "verdict.earned": "What you got back",
      "verdict.couldBe": "What you could have got",

      /* --- section headings --- */
      "section.overview": "All your months, put together",
      "section.rules": "Three rules only. Mummy ask this much, can or not?",
      "section.categories": "Where all your money run away to",
      "section.strategist": "Mummy's plan for next month",
      "section.suboptimal": "The aiyo list — every time you use wrong card",
      "section.cards": "Your cards, one by one",
      "section.method": "Don't believe Mummy? See how she count",

      /* --- table headers --- */
      "th.chargedTo": "You anyhow use",
      "th.betterCard": "Should have used",
      "th.missed": "Throw away",

      /* --- flags --- */
      "flag.minMet": "okay lah",
      "flag.minMissed": "never hit",
      "flag.capHit": "full already",
      "flag.capHeadroom": "still got room",
      "table.allOptimal":
        "Wah, every single one already on the best card. This month Mummy cannot scold you.",

      /* --- strategist --- */
      "advisor.notRun": "Mummy never look at this month yet.",
      "advisor.runBtn": "Ask Mummy to look",
      "advisor.stalePlan": "This plan is in the boring analyst voice.",
      "advisor.rerunBtn": "Ask Mummy to look again",
      "chat.title": "Talk back to Mummy",
      "chat.empty":
        "Complain to Mummy, ask her why, argue also can. If she change her mind, the plan " +
        "up there change also - but the engine must check the numbers first hor.",
      "chat.placeholder": "e.g. Mummy, I never bring the OCBC card out one",
      "chat.waiting": "Mummy checking the numbers, don't rush her…",
      "chat.send": "Tell Mummy",
      "chat.chips": [
        "Why cannot put makan on the 6% card?",
        "I don't want to use OCBC lah.",
        "Too many rules, give me 2 only.",
        "Which rule going to fail first ah?",
      ],

      /* --- prompts / confirms --- */
      "prompt.rename": "This month call what? Give it a proper name:",
      "confirm.removeMonth": (label) => `Sure you want to throw away "${label}"? Cannot get back one hor.`,
      "confirm.clearAll": (n) => `Throw away all ${n} months? Then Mummy got nothing to look at.`,

      /* --- markup-producing --- */
      "verdict.lede": (s, money) =>
        h`Wah lau eh. You spend ${money(s.total_spend)} over ${s.transaction_count} times, and ` +
        h`only take back <strong>${s.actual_yield_pct}%</strong>. Same shopping, same cards ` +
        h`already inside your own wallet - can be <strong class="pos">${s.optimal_yield_pct}%</strong> you know!`,

      /* --- plain formatters --- */
      "verdict.sub": (data) =>
        data.summary.already_optimal
          ? "Aiyo, this time Mummy cannot find anything to scold. Every purchase already on the best card - steady lah."
          : `${data.suboptimal_count} out of ${data.summary.transaction_count} purchases you anyhow whack on the wrong card. ` +
            `${cash(data.summary.missed_value)} gone - that is about ${plates(data.summary.missed_value)} plate of ` +
            `chicken rice at $${PLATE.toFixed(2)}. You think money grow on tree ah?`,
      "rule.next": (rule) => `next month hor, ${rule.rate_label}`,
      "tune.summaryLine": (config, names) =>
        `${config.transaction_count} times shopping, about $${config.total_spend.toLocaleString()} ` +
        `on ${names.join(", ") || "no card at all"}.`,
      "habit.spread": "Anyhow use, all over the place",
      "habit.bestRate": "Smart one - best card every time",
      "habit.primary": (name) => `Everything on ${name} only`,
      "habit.mostly": (name) => `Mostly ${name}, sometimes anyhow`,
    },
  };

  function currentPersona() {
    return document.documentElement.getAttribute("data-mode") === "mum" ? "mum" : "analyst";
  }

  /** Resolved value; mum falls back to analyst for any key it does not override. */
  function copyOf(key) {
    const persona = COPY[currentPersona()];
    return persona && key in persona ? persona[key] : COPY.analyst[key];
  }

  function t(key) {
    const value = copyOf(key);
    return typeof value === "function" ? value() : value;
  }

  function fmt(key, ...args) {
    const value = copyOf(key);
    return typeof value === "function" ? value(...args) : value;
  }

  /** Sweep the static nodes. An unknown key leaves the authored DOM untouched. */
  function applyCopy(root) {
    const scope = root || document;
    scope.querySelectorAll("[data-copy]").forEach((el) => {
      const value = copyOf(el.dataset.copy);
      if (typeof value === "string") el.textContent = value;
    });
    scope.querySelectorAll("[data-copy-aria]").forEach((el) => {
      const value = copyOf(el.dataset.copyAria);
      if (typeof value === "string") el.setAttribute("aria-label", value);
    });
    scope.querySelectorAll("[data-copy-placeholder]").forEach((el) => {
      const value = copyOf(el.dataset.copyPlaceholder);
      if (typeof value === "string") el.placeholder = value;
    });
  }

  window.CopyText = { t, fmt, applyCopy, currentPersona, cash, plates, h, esc, PLATE, COPY };
})();
