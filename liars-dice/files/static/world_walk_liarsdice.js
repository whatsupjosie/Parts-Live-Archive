/* Liar's Dice at the Vortex Bar booth: the table you play on when you click the booth in the walker.
 *
 * All rules, dice and opponents live on the PubCast host (modules/liars_dice.py, served by modules/liars_dice_api.py).
 * This file only shows the table the host sends and sends back what you choose. It never rolls a die or decides a
 * result, and it never holds anyone else's dice: the host does not send them until a call turns every cup over.
 *
 *   window.PubCastLiarsDice.open()   show the table (resumes a saved game, or offers a new one)
 *   window.PubCastLiarsDice.close()  hide it; the game stays saved on the host
 *   window.PubCastLiarsDice.isOpen()
 *
 * The walker stops taking walking keys while it is open (world_walk.html inputLocked). Esc closes it.
 * Sound is made in the browser (Web Audio, no files). The dice are drawn with CSS: no art assets are needed.
 */
(function (global) {
  "use strict";

  const API = "/api/minigames/liarsdice";
  const MUTE_KEY = "pubcast.liarsdice.muted";
  const PIPS = { 1: [4], 2: [0, 8], 3: [0, 4, 8], 4: [0, 2, 6, 8], 5: [0, 2, 4, 6, 8], 6: [0, 2, 3, 5, 6, 8] };
  const FACE_NAMES = ["", "ones", "twos", "threes", "fours", "fives", "sixes"];
  const STEP_MS = 850, STEP_MS_REDUCED = 300;

  const reduced = () => !!(global.matchMedia && global.matchMedia("(prefers-reduced-motion: reduce)").matches);
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  const S = {
    open: false, root: null, body: null, status: null, errorBox: null, game: null, busy: false, loadFailed: false,
    pick: null, setup: { opponents: 2, wild: false }, confirmReset: false, ticker: null, token: 0,
    muted: false, ctx: null, returnFocus: null, titleEl: null,
  };
  try { S.muted = global.localStorage.getItem(MUTE_KEY) === "1"; } catch (e) { /* storage may be unavailable */ }

  /* ---------------------------------------------------------------- sound */
  function audio() {
    if (S.ctx) return S.ctx;
    const AC = global.AudioContext || global.webkitAudioContext;
    if (!AC) return null;
    try { S.ctx = new AC(); } catch (e) { S.ctx = null; }
    return S.ctx;
  }
  function withAudio(fn) {
    if (S.muted) return;
    const ctx = audio(); if (!ctx) return;
    try { if (ctx.state === "suspended") ctx.resume(); fn(ctx, ctx.currentTime); } catch (e) { /* sound is optional */ }
  }
  function noiseBuffer(ctx, seconds) {
    const n = Math.floor(ctx.sampleRate * seconds), buf = ctx.createBuffer(1, n, ctx.sampleRate), d = buf.getChannelData(0);
    for (let i = 0; i < n; i++) d[i] = Math.random() * 2 - 1;
    return buf;
  }
  /* A jangly rattle: a run of short, bright knocks at uneven gaps. */
  function soundRattle() {
    withAudio((ctx, t0) => {
      const buf = noiseBuffer(ctx, 0.05), knocks = 9 + Math.floor(Math.random() * 5);
      let t = t0;
      for (let i = 0; i < knocks; i++) {
        const src = ctx.createBufferSource(), band = ctx.createBiquadFilter(), gain = ctx.createGain();
        src.buffer = buf; band.type = "bandpass"; band.frequency.value = 1800 + Math.random() * 3200; band.Q.value = 3.5;
        gain.gain.setValueAtTime(0.0001, t);
        gain.gain.exponentialRampToValueAtTime(0.22 + Math.random() * 0.18, t + 0.004);
        gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.05);
        src.connect(band); band.connect(gain); gain.connect(ctx.destination); src.start(t);
        t += 0.035 + Math.random() * 0.055;
      }
    });
  }
  function tone(ctx, t, from, to, len, type, vol) {
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.type = type; o.frequency.setValueAtTime(from, t); o.frequency.exponentialRampToValueAtTime(to, t + len);
    g.gain.setValueAtTime(vol, t); g.gain.exponentialRampToValueAtTime(0.0001, t + len);
    o.connect(g); g.connect(ctx.destination); o.start(t); o.stop(t + len + 0.02);
  }
  const soundThud = () => withAudio((c, t) => tone(c, t, 150, 45, 0.22, "sine", 0.5));              // a cup set down
  const soundBid = () => withAudio((c, t) => tone(c, t, 640, 640, 0.07, "triangle", 0.18));
  const soundLoss = () => withAudio((c, t) => { tone(c, t, 330, 165, 0.35, "sawtooth", 0.16); });
  const soundWin = () => withAudio((c, t) => { [523, 659, 784, 1046].forEach((f, i) => tone(c, t + i * 0.11, f, f, 0.2, "triangle", 0.2)); });

  /* ------------------------------------------------------------- the host */
  async function http(method, path, body) {
    let res;
    try {
      res = await fetch(API + path, { method, cache: "no-store", headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
    } catch (e) {
      return { ok: false, status: 0, data: { detail: "Can't reach the PubCast host. Your game is kept there; try again in a moment." } };
    }
    let data = null;
    try { data = await res.json(); } catch (e) { /* not JSON */ }
    if (res.ok && data && data.ok) return { ok: true, status: res.status, data };
    let detail = data && data.detail;
    if (res.status === 401) detail = "You need to be signed in to play. Sign in, then come back to the table.";
    return { ok: false, status: res.status, data: { detail: detail || "The host answered with an error (" + res.status + ")." } };
  }
  const detailText = d => (d && typeof d.detail === "object" && d.detail) ? d.detail.message || "That did not work." : String((d && d.detail) || "That did not work.");

  /* --------------------------------------------------------------- helpers */
  function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; }
  function button(text, fn, cls, opts) {
    const b = el("button", cls, text); b.type = "button";
    if (opts && opts.disabled) b.disabled = true;
    if (opts && opts.label) b.setAttribute("aria-label", opts.label);
    if (opts && opts.pressed !== undefined) b.setAttribute("aria-pressed", String(!!opts.pressed));
    if (opts && opts.focus) b.dataset.autofocus = "1";
    if (opts && opts.key) b.dataset.key = opts.key;
    b.addEventListener("click", fn); return b;
  }
  function die(face, cls) {
    const d = el("span", "ld-die" + (cls ? " " + cls : ""));
    d.setAttribute("role", "img"); d.setAttribute("aria-label", "die showing " + face);
    const on = new Set(PIPS[face] || []);
    for (let i = 0; i < 9; i++) d.appendChild(el("i", on.has(i) ? "pip" : "gap"));
    return d;
  }
  const seatOf = (g, id) => (g.seats || []).find(s => s.id === id);
  const nameOf = (g, id) => { const s = seatOf(g, id); return !s ? id : s.human ? "You" : s.name; };
  const verb = (g, id, you, other) => (seatOf(g, id) && seatOf(g, id).human ? you : other);
  const bidText = (count, face) => count + " × " + face + (count === 1 ? " (one " + FACE_NAMES[face].replace(/s$/, "") + ")" : " (" + FACE_NAMES[face] + ")");

  /* The raise rule, as the host enforces it: more dice of any face, or the same number on a higher face. */
  function isRaise(prev, count, face, total) {
    if (!Number.isInteger(count) || !Number.isInteger(face) || face < 1 || face > 6 || count < 1 || count > total) return false;
    if (!prev) return true;
    return count > prev.count || (count === prev.count && face > prev.face);
  }
  function lowestRaise(prev, total) {
    if (!prev) return { count: 1, face: 1 };
    if (prev.face < 6 && prev.count <= total) return { count: prev.count, face: prev.face + 1 };
    return prev.count + 1 <= total ? { count: prev.count + 1, face: 1 } : null;
  }

  /* ----------------------------------------------------------------- style */
  const CSS = `
#ldice{position:fixed;inset:0;z-index:50;display:flex;align-items:center;justify-content:center;background:rgba(6,6,12,.72);padding:12px;font:14px/1.4 system-ui,-apple-system,"Segoe UI",sans-serif}
#ldice[hidden]{display:none}
#ldice .ld-card{background:var(--panel,#1b1d27);color:var(--ink,#eceef6);border:1px solid var(--accent,#ffd36b);border-radius:16px;width:min(720px,100%);max-height:100%;overflow:auto;box-shadow:0 18px 60px rgba(0,0,0,.6);display:flex;flex-direction:column}
#ldice header{display:flex;align-items:center;gap:8px;padding:12px 16px;border-bottom:1px solid var(--line,#343748)}
#ldice header h2{margin:0;font-size:17px;flex:1}
#ldice .ld-main{padding:14px 16px;display:flex;flex-direction:column;gap:14px}
#ldice .ld-status{min-height:1.4em;color:var(--dim,#a3a8bd)}
#ldice .ld-error{color:var(--low,#ff8a7a);border:1px solid var(--low,#ff8a7a);border-radius:8px;padding:8px 10px}
#ldice .ld-seats{display:flex;gap:8px;flex-wrap:wrap}
#ldice .ld-seat{flex:1 1 130px;border:1px solid var(--line,#343748);border-radius:12px;padding:8px 10px;min-width:120px}
#ldice .ld-seat.turn{border-color:var(--accent,#ffd36b);box-shadow:0 0 0 1px var(--accent,#ffd36b) inset}
#ldice .ld-seat.out{opacity:.5}
#ldice .ld-seat .nm{font-weight:600}
#ldice .ld-cups{display:flex;gap:4px;margin-top:6px;flex-wrap:wrap;min-height:14px}
#ldice .ld-cup{width:12px;height:14px;border-radius:2px 2px 5px 5px;background:var(--accent,#ffd36b);opacity:.85}
#ldice .ld-table{border:1px solid var(--line,#343748);border-radius:14px;padding:12px;background:var(--bg,#12131a);display:flex;flex-direction:column;gap:10px;align-items:center}
#ldice .ld-bid{font-size:18px;font-weight:700;text-align:center}
#ldice .ld-hand{display:flex;gap:8px;flex-wrap:wrap;justify-content:center}
#ldice .ld-hands{display:flex;flex-direction:column;gap:8px;width:100%}
#ldice .ld-hands .row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
#ldice .ld-hands .row .who{min-width:90px;font-weight:600}
#ldice .ld-die{display:inline-grid;grid-template-columns:repeat(3,1fr);grid-template-rows:repeat(3,1fr);width:44px;height:44px;padding:5px;gap:2px;border-radius:9px;background:#f6f3ea;color:#1c1e2a;border:1px solid #bdb8a8;box-shadow:0 2px 4px rgba(0,0,0,.35)}
#ldice .ld-die.small{width:30px;height:30px;padding:3px;border-radius:6px;gap:1px}
#ldice .ld-die i{display:block;border-radius:50%}
#ldice .ld-die i.pip{background:#1c1e2a}
#ldice .ld-die.hit{outline:3px solid var(--good,#6be5a0);outline-offset:1px}
#ldice .ld-die.wild{outline:3px dashed var(--accent,#ffd36b);outline-offset:1px}
#ldice .ld-die.miss{opacity:.45}
#ldice .ld-die.tumble{animation:ldtumble .7s cubic-bezier(.2,.7,.3,1) both}
@keyframes ldtumble{0%{transform:translateY(-12px) rotate(-300deg) scale(.75);opacity:0}60%{transform:translateY(2px) rotate(20deg) scale(1.05);opacity:1}100%{transform:none;opacity:1}}
#ldice .ld-controls{display:flex;flex-direction:column;gap:10px;width:100%;align-items:center}
#ldice .ld-pick{display:flex;gap:6px;flex-wrap:wrap;justify-content:center;align-items:center}
#ldice .ld-count{min-width:2.4em;text-align:center;font-size:20px;font-weight:700}
#ldice .ld-face{padding:2px;min-height:0;border-radius:10px}
#ldice .ld-face[aria-pressed="true"]{border-color:var(--accent,#ffd36b);box-shadow:0 0 0 2px var(--accent,#ffd36b)}
#ldice .ld-actions{display:flex;gap:10px;flex-wrap:wrap;justify-content:center}
#ldice .ld-actions button{padding:8px 18px;font-weight:600}
#ldice .ld-call{border-color:var(--low,#ff8a7a)}
#ldice .ld-hint{color:var(--dim,#a3a8bd);font-size:13px;text-align:center}
#ldice .ld-setup{display:flex;flex-direction:column;gap:12px}
#ldice .ld-setup fieldset{border:1px solid var(--line,#343748);border-radius:10px;padding:8px 12px}
#ldice .ld-setup label{margin-right:14px}
#ldice .ld-rules{color:var(--dim,#a3a8bd);font-size:13px;margin:0;padding-left:18px}
#ldice .ld-result{font-size:16px;font-weight:600;text-align:center}
@media (prefers-reduced-motion:reduce){#ldice .ld-die.tumble{animation:none}}
`;
  function injectStyle() {
    if (document.getElementById("ldice-style")) return;
    const s = document.createElement("style"); s.id = "ldice-style"; s.textContent = CSS; document.head.appendChild(s);
  }

  /* ---------------------------------------------------------------- shell */
  function build() {
    injectStyle();
    const root = el("div"); root.id = "ldice"; root.hidden = true;
    const card = el("div", "ld-card"); card.setAttribute("role", "dialog"); card.setAttribute("aria-modal", "true"); card.setAttribute("aria-labelledby", "ldice-title");
    const head = el("header"); const title = el("h2", null, "Liar’s Dice"); title.id = "ldice-title";
    const mute = button(S.muted ? "Sound off" : "Sound on", () => {
      S.muted = !S.muted; mute.textContent = S.muted ? "Sound off" : "Sound on"; mute.setAttribute("aria-pressed", String(!S.muted));
      try { global.localStorage.setItem(MUTE_KEY, S.muted ? "1" : "0"); } catch (e) { /* ignore */ }
    }, "ld-mute", { pressed: !S.muted });
    const leave = button("Leave table", () => close(), "ld-leave", { label: "Leave the table (your game is kept)" });
    head.append(title, mute, leave);
    const main = el("div", "ld-main");
    const status = el("div", "ld-status"); status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite");
    const err = el("div", "ld-error"); err.setAttribute("role", "alert"); err.hidden = true;
    const body = el("div", "ld-body");
    main.append(status, err, body);
    card.append(head, main); root.appendChild(card); document.body.appendChild(root);
    S.root = root; S.body = body; S.status = status; S.errorBox = err; S.titleEl = title;
    root.addEventListener("pointerdown", e => e.stopPropagation());       // clicks here never reach the walker underneath
    root.addEventListener("click", e => { if (e.target === root) close(); });
  }
  const setStatus = t => { if (S.status) S.status.textContent = t || ""; };
  function setError(t) { S.errorBox.hidden = !t; S.errorBox.textContent = t || ""; }
  function focusFirst() {
    if (S.focusKey) {
      const k = S.body.querySelector('[data-key="' + S.focusKey + '"]:not(:disabled)'); S.focusKey = null;
      if (k) { k.focus({ preventScroll: true }); return; }
    }
    const t = S.body.querySelector("[data-autofocus]:not(:disabled)") || S.body.querySelector("button:not(:disabled), input:not(:disabled)");
    if (t) t.focus({ preventScroll: true });
  }

  /* --------------------------------------------------------------- screens */
  function renderLoading(text) { S.body.textContent = ""; S.body.appendChild(el("p", "ld-hint", text)); }

  function renderOffline(message) {
    S.body.textContent = ""; setStatus("");
    setError(message);
    const row = el("div", "ld-actions");
    row.append(button("Try again", () => load(), null, { focus: true }), button("Leave table", () => close()));
    S.body.appendChild(row); focusFirst();
  }

  function renderSetup() {
    S.body.textContent = ""; S.confirmReset = false;
    const box = el("div", "ld-setup");
    box.appendChild(el("p", null, "You sit down with up to three opponents. Everyone starts with five dice under a cup."));
    const rules = el("ul", "ld-rules");
    [
      "On your turn, bid how many dice of one face are on the whole table, counting everyone’s hidden dice.",
      "Each bid must beat the one before: more dice of any face, or the same number on a higher face.",
      "Or call “Liar!”. Every cup is lifted. If the bid was met or beaten, the caller loses a die; if not, the bidder does.",
      "Whoever loses a die opens the next round. Out of dice means out of the game. Last one standing wins.",
    ].forEach(t => rules.appendChild(el("li", null, t)));
    box.appendChild(rules);

    const fs = el("fieldset"); const lg = el("legend", null, "Opponents"); fs.appendChild(lg);
    [1, 2, 3].forEach(n => {
      const id = "ld-opp-" + n, lab = el("label"), r = el("input"); r.type = "radio"; r.name = "ld-opp"; r.value = String(n); r.id = id;
      r.checked = S.setup.opponents === n; if (r.checked) r.dataset.autofocus = "1";
      r.addEventListener("change", () => { S.setup.opponents = n; });
      lab.append(r, document.createTextNode(" " + n)); fs.appendChild(lab);
    });
    box.appendChild(fs);
    const wl = el("label"), w = el("input"); w.type = "checkbox"; w.checked = S.setup.wild;
    w.addEventListener("change", () => { S.setup.wild = w.checked; });
    wl.append(w, document.createTextNode(" Ones are wild (they count as any face, except when someone bids on ones)"));
    box.appendChild(wl);
    box.appendChild(button("Sit down and roll", () => startGame(false), "ld-start"));
    S.body.appendChild(box); setStatus(""); focusFirst();
  }

  function seatStrip(g) {
    const strip = el("div", "ld-seats");
    g.seats.forEach(s => {
      const c = el("div", "ld-seat" + (g.turn === s.id ? " turn" : "") + (s.out ? " out" : ""));
      c.appendChild(el("div", "nm", s.human ? "You" : s.name));
      const cups = el("div", "ld-cups");
      for (let i = 0; i < s.remaining; i++) cups.appendChild(el("span", "ld-cup"));
      cups.setAttribute("role", "img"); cups.setAttribute("aria-label", s.remaining + (s.remaining === 1 ? " die left" : " dice left"));
      c.append(cups, el("div", "ld-hint", s.out ? "Out" : s.remaining + (s.remaining === 1 ? " die" : " dice") + (g.turn === s.id ? " • bidding" : "")));
      strip.appendChild(c);
    });
    return strip;
  }

  function renderTable(g, opts) {
    opts = opts || {};
    S.body.textContent = ""; S.game = g;
    if (g.phase === "over") S.confirmReset = false;
    S.body.appendChild(seatStrip(g));
    const table = el("div", "ld-table");
    if (opts.ticker !== undefined) {
      const t = el("div", "ld-bid", opts.ticker); t.setAttribute("aria-hidden", "true"); table.appendChild(t);
    } else if (g.phase === "bidding") {
      table.appendChild(el("div", "ld-bid", g.bid ? nameOf(g, g.bid.by) + (g.bid.by === g.you.id ? " bid " : " bids ") + bidText(g.bid.count, g.bid.face) : "Round " + g.round + ": no bid yet"));
    }
    if (g.phase === "bidding" && g.you.dice.length) {
      table.appendChild(el("div", "ld-hint", "Your dice (only you can see them)"));
      const hand = el("div", "ld-hand");
      g.you.dice.forEach(f => hand.appendChild(die(f, opts.tumble && !reduced() ? "tumble" : "")));
      table.appendChild(hand);
    }
    if (g.phase !== "bidding" && g.reveal && !opts.hideReveal) table.appendChild(revealBlock(g));
    if (opts.ticker === undefined) table.appendChild(controls(g));
    S.body.appendChild(table);
    if (opts.ticker === undefined && g.phase !== "over") S.body.appendChild(forgetRow());
    focusFirst();
  }

  /* Walking away keeps the game. Throwing it away takes two clicks. */
  function forgetRow() {
    const row = el("div", "ld-actions");
    if (!S.confirmReset) row.appendChild(button("Forget this game", () => { S.confirmReset = true; S.focusKey = "forget-no"; renderTable(S.game); }, "ld-forget", { key: "forget" }));
    else {
      row.append(el("span", "ld-hint", "Throw this game away?"),
        button("Yes, forget it", () => forgetGame(), "ld-forget-yes", { key: "forget-yes" }),
        button("Keep playing", () => { S.confirmReset = false; S.focusKey = "forget"; renderTable(S.game); }, null, { key: "forget-no" }));
    }
    return row;
  }
  function forgetGame() {
    return act("DELETE", "", null, async () => { S.game = null; S.pick = null; S.confirmReset = false; setStatus("Game forgotten."); renderSetup(); });
  }

  function revealBlock(g) {
    const r = g.reveal, wrap = el("div", "ld-hands");
    const bidderWild = g.wild_ones && r.face !== 1;
    wrap.appendChild(el("div", "ld-bid", nameOf(g, r.caller) + verb(g, r.caller, " call ", " calls ") + (verb(g, r.bidder, "your", nameOf(g, r.bidder) + "’s")) + " bid of " + bidText(r.count, r.face)));
    Object.keys(r.hands).forEach(id => {
      const row = el("div", "row"); row.appendChild(el("span", "who", nameOf(g, id)));
      const hand = el("div", "ld-hand");
      r.hands[id].forEach(f => {
        const isHit = f === r.face, isWild = bidderWild && f === 1;
        hand.appendChild(die(f, "small " + (isHit ? "hit" : isWild ? "wild" : "miss") + (reduced() ? "" : " tumble")));
      });
      row.appendChild(hand); wrap.appendChild(row);
    });
    const verdict = r.bid_true
      ? "There were " + r.matching + ". The bid was good."
      : "There were only " + r.matching + ". The bid was a lie.";
    wrap.appendChild(el("div", "ld-result", verdict + " " + nameOf(g, r.loser) + verb(g, r.loser, " lose a die.", " loses a die.")));
    if (g.phase === "over") wrap.appendChild(el("div", "ld-result", endText(g)));
    return wrap;
  }
  function endText(g) {
    if (g.winner === g.you.id) return "You win. Everyone else is out of dice.";
    if (g.winner) return "You are out of dice. " + nameOf(g, g.winner) + " wins.";
    return "You are out of dice. The game is over.";
  }

  function controls(g) {
    const box = el("div", "ld-controls");
    if (g.phase === "reveal") {
      const a = el("div", "ld-actions"); a.appendChild(button("Next round", () => nextRound(), null, { focus: true })); box.appendChild(a); return box;
    }
    if (g.phase === "over") {
      const a = el("div", "ld-actions");
      a.append(button("Play again", () => { S.setup.opponents = Math.max(1, Math.min(3, g.seats.length - 1)); S.setup.wild = !!g.wild_ones; startGame(false); }, null, { focus: true }),
               button("Set up a new table", () => renderSetup()), button("Leave table", () => close()));
      box.appendChild(a); return box;
    }
    if (g.turn !== g.you.id) { box.appendChild(el("div", "ld-hint", "Waiting for the other players…")); return box; }

    const total = g.total_dice, prev = g.bid;
    if (!S.pick || S.pick.game !== g.game_id || S.pick.move !== g.move || !isRaise(prev, S.pick.count, S.pick.face, total)) {
      const low = lowestRaise(prev, total); S.pick = low ? { game: g.game_id, move: g.move, count: low.count, face: low.face } : { game: g.game_id, move: g.move, count: 0, face: 0 };
    }
    const pick = S.pick;
    const redraw = key => { S.focusKey = key; renderTable(g); };
    if (g.can_raise) {
      const row = el("div", "ld-pick"); row.setAttribute("role", "group"); row.setAttribute("aria-label", "Your bid");
      const minus = button("−", () => { pick.count -= 1; redraw("minus"); }, null, { label: "One fewer die", disabled: pick.count <= 1, key: "minus" });
      const num = el("span", "ld-count", String(pick.count)); num.setAttribute("aria-label", pick.count + " dice");
      const plus = button("+", () => { pick.count += 1; redraw("plus"); }, null, { label: "One more die", disabled: pick.count >= total, key: "plus" });
      row.append(minus, num, plus, el("span", null, "×"));
      for (let f = 1; f <= 6; f++) {
        const fb = button("", () => { pick.face = f; redraw("face-" + f); }, "ld-face", { pressed: pick.face === f, label: FACE_NAMES[f], key: "face-" + f });
        fb.appendChild(die(f, "small")); row.appendChild(fb);
      }
      box.appendChild(row);
      const ok = isRaise(prev, pick.count, pick.face, total);
      box.appendChild(el("div", "ld-hint", prev
        ? "To beat " + bidText(prev.count, prev.face) + ": more dice of any face, or " + prev.count + " on a higher face. " + total + " dice are in play."
        : "Open with any bid. " + total + " dice are in play."));
      const a = el("div", "ld-actions");
      a.appendChild(button(ok ? "Bid " + bidText(pick.count, pick.face) : "Pick a higher bid", () => placeBid(pick.count, pick.face), "ld-bid-btn", { disabled: !ok, focus: true }));
      if (g.can_call) a.appendChild(button("Call Liar!", () => callLiar(), "ld-call"));
      box.appendChild(a);
    } else {
      box.appendChild(el("div", "ld-hint", "Nothing can beat that bid. You can only call."));
      const a = el("div", "ld-actions"); a.appendChild(button("Call Liar!", () => callLiar(), "ld-call", { focus: true })); box.appendChild(a);
    }
    return box;
  }

  /* ---------------------------------------------------------------- actions */
  async function load() {
    const mine = ++S.token; setError(""); renderLoading("Looking for your table…");
    const r = await http("GET", "");
    if (mine !== S.token || !S.open) return;
    if (!r.ok) { renderOffline(detailText(r.data)); return; }
    S.game = r.data.game;
    if (!S.game) renderSetup(); else { renderTable(S.game); setStatus(statusFor(S.game, true)); }
  }
  function statusFor(g, resumed) {
    if (g.phase === "over") return endText(g);
    if (g.phase === "reveal") return "Cups up. " + nameOf(g, g.reveal.loser) + verb(g, g.reveal.loser, " lose a die.", " loses a die.");
    return (resumed ? "Welcome back. " : "") + "Round " + g.round + ". " + (g.turn === g.you.id ? "Your bid." : nameOf(g, g.turn) + " is bidding.");
  }

  /* One request at a time; a second click while one is in flight is ignored (the host would refuse it as stale anyway). */
  async function act(method, path, body, afterOk) {
    if (S.busy) return;
    S.busy = true; setError("");
    const mine = ++S.token;
    const before = S.game;
    S.body.querySelectorAll("button").forEach(b => { b.disabled = true; });
    const r = await http(method, path, body);
    if (mine !== S.token || !S.open) { S.busy = false; return; }
    if (!r.ok) {
      S.busy = false;
      if (r.status === 409 && r.data.detail && typeof r.data.detail === "object" && r.data.detail.game) {
        S.game = r.data.detail.game; renderTable(S.game); setError(detailText(r.data)); setStatus(statusFor(S.game)); return;
      }
      setError(detailText(r.data));
      if (before) renderTable(before); else renderSetup();
      return;
    }
    try { await afterOk(r.data); } finally { S.busy = false; }
  }

  const startGame = replace => act("POST", "/start", { opponents: S.setup.opponents, wild_ones: S.setup.wild, replace: !!replace }, async d => {
    S.game = d.game; S.pick = null; soundRattle();
    renderTable(d.game, { tumble: true }); setStatus(statusFor(d.game));
  });
  const placeBid = (count, face) => act("POST", "/bid", { count, face, expected_move: S.game.move }, d => playback(d));
  const callLiar = () => act("POST", "/call", { expected_move: S.game.move }, d => playback(d));
  const nextRound = () => act("POST", "/next", { expected_move: S.game.move }, d => playback(d));

  /* Replay what happened, one beat at a time, then show the table the host ended on. */
  async function playback(d) {
    const g = d.game, mine = S.token, step = reduced() ? STEP_MS_REDUCED : STEP_MS;
    let tumble = false;
    for (const ev of d.events) {
      if (mine !== S.token || !S.open) return;
      let text = null;
      if (ev.type === "bid") { text = nameOf(g, ev.seat) + verb(g, ev.seat, " bid ", " bids ") + bidText(ev.count, ev.face); soundBid(); }
      else if (ev.type === "call") { text = nameOf(g, ev.seat) + verb(g, ev.seat, " call", " calls") + " Liar on " + verb(g, ev.against, "you", nameOf(g, ev.against)) + "!"; soundThud(); }
      else if (ev.type === "reveal") { text = "Cups up…"; soundRattle(); }
      else if (ev.type === "lost_die") { text = nameOf(g, ev.seat) + verb(g, ev.seat, " lose a die.", " loses a die."); soundLoss(); }
      else if (ev.type === "out") { text = nameOf(g, ev.seat) + verb(g, ev.seat, " are out.", " is out."); }
      else if (ev.type === "over") { text = endText(g); if (ev.human_won) soundWin(); }
      else if (ev.type === "round") { text = "Round " + ev.round + ". " + nameOf(g, ev.first) + verb(g, ev.first, " open.", " opens."); soundRattle(); tumble = true; }
      if (text === null) continue;
      renderTable(g, { ticker: text, hideReveal: true }); setStatus(text);
      await sleep(step);
    }
    if (mine !== S.token || !S.open) return;
    S.game = g; renderTable(g, { tumble }); setStatus(statusFor(g));
  }

  /* ------------------------------------------------------------ open / close */
  function trapKeys(e) {
    if (!S.open) return;
    if (e.type === "keydown") {
      if (e.key === "Escape") { e.preventDefault(); close(); e.stopPropagation(); return; }
      if (e.key === "Tab") {
        const items = Array.from(S.root.querySelectorAll("button:not(:disabled), input:not(:disabled)")).filter(n => n.offsetParent !== null);
        if (!items.length) { e.preventDefault(); } else {
          const first = items[0], last = items[items.length - 1], a = document.activeElement;
          if (!S.root.contains(a)) { e.preventDefault(); first.focus(); }
          else if (e.shiftKey && a === first) { e.preventDefault(); last.focus(); }
          else if (!e.shiftKey && a === last) { e.preventDefault(); first.focus(); }
        }
      }
    }
    e.stopPropagation();          // walking keys, shortcuts and Esc-to-go-back never see keys typed at the table
  }

  function open() {
    if (S.open) return;
    if (!S.root) build();
    S.open = true; S.busy = false; S.returnFocus = document.activeElement;
    S.root.hidden = false;
    global.addEventListener("keydown", trapKeys, true); global.addEventListener("keyup", trapKeys, true);
    audio();                                    // opened by a click, so the browser lets sound start
    load();
  }
  function close() {
    if (!S.open) return;
    S.open = false; S.token++; S.busy = false;
    S.root.hidden = true; setError(""); setStatus("");
    global.removeEventListener("keydown", trapKeys, true); global.removeEventListener("keyup", trapKeys, true);
    const f = S.returnFocus; S.returnFocus = null;
    if (f && typeof f.focus === "function" && document.contains(f)) { try { f.focus({ preventScroll: true }); } catch (e) { /* ignore */ } }
  }

  global.PubCastLiarsDice = { open, close, isOpen: () => S.open };
})(window);
