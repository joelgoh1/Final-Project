/* fx.js - the fun layer: flying-money cursor, coin bursts, money rain, confetti, count-ups.
   Pure browser JS, no dependencies. Everything here is cosmetic and can be switched off. */

export const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
export const finePointer = window.matchMedia("(pointer: fine)").matches;

const MAX_PARTICLES = 500;
const particles = [];
let canvas, ctx, dpr = 1;
let running = false;

// --------------------------------------------------------------------- canvas
function ensureCanvas() {
  if (canvas) return;
  canvas = document.createElement("canvas");
  canvas.id = "fx-layer";
  canvas.setAttribute("aria-hidden", "true");
  document.body.appendChild(canvas);
  ctx = canvas.getContext("2d");
  resize();
  window.addEventListener("resize", resize);
}

function resize() {
  dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.floor(window.innerWidth * dpr);
  canvas.height = Math.floor(window.innerHeight * dpr);
  canvas.style.width = window.innerWidth + "px";
  canvas.style.height = window.innerHeight + "px";
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

function spawn(p) {
  ensureCanvas();
  if (particles.length >= MAX_PARTICLES) particles.splice(0, particles.length - MAX_PARTICLES + 1);
  particles.push(p);
  if (!running) { running = true; requestAnimationFrame(tick); }
}

let last = 0;
function tick(now) {
  const dt = Math.min(0.05, (now - (last || now)) / 1000);
  last = now;
  ctx.clearRect(0, 0, window.innerWidth, window.innerHeight);
  for (let i = particles.length - 1; i >= 0; i--) {
    const p = particles[i];
    p.life -= dt;
    if (p.life <= 0 || p.y > window.innerHeight + 60) { particles.splice(i, 1); continue; }
    p.update(p, dt);
    p.draw(p, ctx);
  }
  if (particles.length) requestAnimationFrame(tick);
  else { running = false; last = 0; ctx.clearRect(0, 0, window.innerWidth, window.innerHeight); }
}

// ------------------------------------------------------------- particle kinds
const GREEN = "#2fbf71", DARK_GREEN = "#1b7a4a", GOLD = "#f5c542", GOLD_DARK = "#c9931a";
const CONFETTI = ["#6f8dff", "#4fd193", "#f5c542", "#ff7b70", "#c17dff", "#4fd1e0"];

function sparkle(x, y) {
  const a = Math.random() * Math.PI * 2, s = 30 + Math.random() * 60;
  return {
    x, y, vx: Math.cos(a) * s, vy: Math.sin(a) * s - 40, life: 0.6 + Math.random() * 0.4, size: 9 + Math.random() * 6,
    update(p, dt) { p.x += p.vx * dt; p.y += p.vy * dt; p.vy += 40 * dt; },
    draw(p, c) {
      c.globalAlpha = Math.max(0, p.life);
      c.fillStyle = GREEN; c.font = `bold ${p.size}px ui-monospace, monospace`;
      c.fillText("$", p.x, p.y);
      c.globalAlpha = 1;
    },
  };
}

function coin(x, y, power = 1) {
  const a = -Math.PI / 2 + (Math.random() - 0.5) * 1.6, s = (180 + Math.random() * 220) * power;
  return {
    x, y, vx: Math.cos(a) * s, vy: Math.sin(a) * s, r: 5 + Math.random() * 4, spin: Math.random() * Math.PI, life: 1.6 + Math.random() * 0.6,
    update(p, dt) { p.x += p.vx * dt; p.y += p.vy * dt; p.vy += 900 * dt; p.spin += 8 * dt; },
    draw(p, c) {
      c.globalAlpha = Math.min(1, p.life * 1.5);
      const squash = Math.abs(Math.cos(p.spin));
      c.beginPath(); c.ellipse(p.x, p.y, p.r * Math.max(0.15, squash), p.r, 0, 0, Math.PI * 2);
      c.fillStyle = GOLD; c.fill(); c.lineWidth = 1.5; c.strokeStyle = GOLD_DARK; c.stroke();
      c.globalAlpha = 1;
    },
  };
}

function bill(x, y) {
  return {
    x, y, vx: 0, vy: 60 + Math.random() * 90, w: 26 + Math.random() * 10, h: 14, rot: Math.random() * Math.PI, sway: Math.random() * Math.PI * 2,
    life: 6,
    update(p, dt) { p.sway += 3 * dt; p.x += Math.sin(p.sway) * 40 * dt; p.y += p.vy * dt; p.rot += 1.5 * dt; },
    draw(p, c) {
      c.save(); c.translate(p.x, p.y); c.rotate(Math.sin(p.rot) * 0.6);
      c.fillStyle = GREEN; c.fillRect(-p.w / 2, -p.h / 2, p.w, p.h);
      c.strokeStyle = DARK_GREEN; c.lineWidth = 1; c.strokeRect(-p.w / 2 + 2, -p.h / 2 + 2, p.w - 4, p.h - 4);
      c.fillStyle = "#e6ffe9"; c.font = "bold 9px ui-monospace, monospace"; c.textAlign = "center"; c.textBaseline = "middle"; c.fillText("$", 0, 0.5);
      c.restore();
    },
  };
}

function confettiPiece(x, y) {
  const a = -Math.PI / 2 + (Math.random() - 0.5) * 2.4, s = 250 + Math.random() * 350;
  return {
    x, y, vx: Math.cos(a) * s, vy: Math.sin(a) * s, w: 6 + Math.random() * 5, h: 3 + Math.random() * 4, rot: Math.random() * 6, vr: (Math.random() - 0.5) * 12,
    color: CONFETTI[Math.floor(Math.random() * CONFETTI.length)], life: 2.4 + Math.random(),
    update(p, dt) { p.x += p.vx * dt; p.y += p.vy * dt; p.vy += 500 * dt; p.vx *= 0.99; p.rot += p.vr * dt; },
    draw(p, c) {
      c.save(); c.globalAlpha = Math.min(1, p.life); c.translate(p.x, p.y); c.rotate(p.rot);
      c.fillStyle = p.color; c.fillRect(-p.w / 2, -p.h / 2, p.w, p.h); c.restore();
    },
  };
}

// ----------------------------------------------------------------- public fx
export function coinBurst(x, y, count = 10, power = 1) {
  if (reducedMotion) return;
  for (let i = 0; i < count; i++) spawn(coin(x, y, power));
}

export function sparkleAt(x, y, count = 1) {
  if (reducedMotion) return;
  for (let i = 0; i < count; i++) spawn(sparkle(x, y));
}

export function moneyRain(durationMs = 2000, density = 1) {
  if (reducedMotion) return;
  const start = performance.now();
  const drop = () => {
    const n = Math.round(3 * density);
    for (let i = 0; i < n; i++) spawn(bill(Math.random() * window.innerWidth, -20 - Math.random() * 40));
    if (performance.now() - start < durationMs) setTimeout(drop, 70);
  };
  drop();
}

export function confetti(x = window.innerWidth / 2, y = window.innerHeight * 0.35, count = 140) {
  if (reducedMotion) return;
  for (let i = 0; i < count; i++) spawn(confettiPiece(x + (Math.random() - 0.5) * 80, y));
}

/** Animate a number into an element. `format` turns the value into display text. */
export function countUp(el, target, { duration = 900, format = (v) => v.toFixed(2) } = {}) {
  if (!el) return;
  if (reducedMotion) { el.textContent = format(target); return; }
  const from = parseFloat(String(el.dataset.fxValue || "0")) || 0;
  const start = performance.now();
  el.dataset.fxValue = String(target);
  const step = (now) => {
    const t = Math.min(1, (now - start) / duration);
    const eased = 1 - Math.pow(1 - t, 3);
    el.textContent = format(from + (target - from) * eased);
    if (t < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

// ------------------------------------------------------- flying money cursor
const BILL_SVG = `
<svg viewBox="0 0 64 44" width="64" height="44" xmlns="http://www.w3.org/2000/svg">
  <g class="wing wing-l" transform-origin="24 18">
    <path d="M24 18 C12 2, 2 6, 4 18 C8 22, 16 22, 24 18 Z" fill="#f6f7fb" stroke="#c8ccd8" stroke-width="1.2"/>
    <path d="M22 17 C14 8, 8 10, 8 16" fill="none" stroke="#c8ccd8" stroke-width="1"/>
  </g>
  <g class="wing wing-r" transform-origin="40 18">
    <path d="M40 18 C52 2, 62 6, 60 18 C56 22, 48 22, 40 18 Z" fill="#f6f7fb" stroke="#c8ccd8" stroke-width="1.2"/>
    <path d="M42 17 C50 8, 56 10, 56 16" fill="none" stroke="#c8ccd8" stroke-width="1"/>
  </g>
  <g class="note">
    <rect x="18" y="14" width="28" height="18" rx="2" fill="#2fbf71" stroke="#1b7a4a" stroke-width="1.5"/>
    <rect x="20.5" y="16.5" width="23" height="13" rx="1" fill="none" stroke="#9ff0c4" stroke-width="1"/>
    <circle cx="32" cy="23" r="4.2" fill="#1b7a4a"/>
    <text x="32" y="25.6" text-anchor="middle" font-family="ui-monospace, monospace" font-weight="700" font-size="7" fill="#e6ffe9">$</text>
  </g>
  <circle class="hotspot" cx="4" cy="4" r="2.2" fill="#fff" stroke="#1b7a4a" stroke-width="1"/>
</svg>`;

let cursorEl = null, cursorOn = false;
let px = -100, py = -100, lastX = -100, lastY = -100, vx = 0, vy = 0, lastMove = 0, trailAcc = 0;

function ensureCursor() {
  if (cursorEl) return;
  cursorEl = document.createElement("div");
  cursorEl.id = "money-cursor";
  cursorEl.setAttribute("aria-hidden", "true");
  cursorEl.innerHTML = BILL_SVG;
  document.body.appendChild(cursorEl);

  const onMove = (e) => {
    if (!cursorOn) return;
    const now = performance.now();
    const dt = Math.max(8, now - lastMove) / 1000;
    vx = (e.clientX - lastX) / dt; vy = (e.clientY - lastY) / dt;
    lastX = px = e.clientX; lastY = py = e.clientY; lastMove = now;
    cursorEl.style.opacity = "1";
    // Tilt into the direction of travel, like a bill caught in a draft.
    const tilt = Math.max(-28, Math.min(28, vx / 40));
    const speed = Math.hypot(vx, vy);
    cursorEl.style.transform = `translate(${px - 4}px, ${py - 4}px) rotate(${tilt}deg)`;
    cursorEl.classList.toggle("fast", speed > 900);
    trailAcc += speed * dt;
    if (trailAcc > 140) { trailAcc = 0; sparkleAt(px + 30, py + 22); }
  };
  window.addEventListener("pointermove", onMove, { passive: true });
  // HTML5 drag-and-drop swallows pointermove; keep the bill on the drag ghost's tail.
  window.addEventListener("dragover", (e) => { if (e.clientX || e.clientY) onMove(e); }, { passive: true });
  window.addEventListener("pointerdown", (e) => {
    if (!cursorOn) return;
    cursorEl.classList.add("press");
    coinBurst(e.clientX, e.clientY, 7, 0.8);
  });
  window.addEventListener("pointerup", () => cursorEl.classList.remove("press"));
  document.addEventListener("mouseleave", () => { cursorEl.style.opacity = "0"; });
  document.addEventListener("mouseenter", () => { if (cursorOn) cursorEl.style.opacity = "1"; });
}

export function setMoneyCursor(on) {
  cursorOn = Boolean(on) && finePointer && !reducedMotion;
  document.documentElement.classList.toggle("fun-cursor", cursorOn);
  if (cursorOn) { ensureCursor(); cursorEl.style.opacity = px < 0 ? "0" : "1"; }
  else if (cursorEl) cursorEl.style.opacity = "0";
  return cursorOn;
}

export function isMoneyCursor() { return cursorOn; }
