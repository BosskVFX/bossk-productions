// KICKBOXER: REBIRTH — "The Door" playable slice.
// 2.5D brawler: walk the alley, beat the thugs, beat the Doorman, earn tournament entry.
'use strict';

const cv = document.getElementById('game');
const ctx = cv.getContext('2d');
const W = 1280, H = 720;      // logical size; the canvas renders at 1920x1080
const FLOOR_TOP = 500, FLOOR_BOT = 690;   // walkable depth band (screen y of feet)
const BG_H = 900;                          // background drawn this tall, anchored to the bottom

// ---------------------------------------------------------------- assets
const META = window.SPRITE_META || {};
const IMG = {};
function loadImg(src) { const i = new Image(); i.src = src; return i; }
for (const name in META) META[name].forEach(f => { if (f) IMG[f.f] = loadImg('assets/sprites/' + f.f); });
const BG = loadImg('assets/bg_alley.jpg');

const SFX = {};
['punch', 'hit2', 'heavy', 'whoosh', 'flame', 'cheer'].forEach(n => { SFX[n] = 'assets/sfx/' + n + '.mp3'; });
// M toggles all sound; ?mute=1 starts muted.
let MUTED = new URLSearchParams(location.search).has('mute');
function sfx(n, vol = 0.8) {
  if (MUTED) return;
  try { const a = new Audio(SFX[n]); a.volume = vol; a.play().catch(() => {}); } catch (e) {}
}
const music = { fight: new Audio('assets/sfx/music_fight.mp3'), boss: new Audio('assets/sfx/music_boss.mp3'), cur: null };
for (const k of ['fight', 'boss']) { music[k].loop = true; music[k].volume = 0.35; }
function playMusic(k) {
  if (music.cur === k) return;
  if (music.cur) music[music.cur].pause();
  music.cur = k;
  if (k && !MUTED) { music[k].currentTime = 0; music[k].play().catch(() => {}); }
}

// ---------------------------------------------------------------- input
const keys = {}, pressed = {};
const KEYMAP = {
  ArrowLeft: 'left', a: 'left', A: 'left', ArrowRight: 'right', d: 'right', D: 'right',
  ArrowUp: 'up', w: 'up', W: 'up', ArrowDown: 'down', s: 'down', S: 'down',
  j: 'punch', J: 'punch', k: 'kick', K: 'kick', l: 'knee', L: 'knee', u: 'spin', U: 'spin',
  ' ': 'block', e: 'clinch', E: 'clinch', f: 'flame', F: 'flame', Shift: 'dodge',
  Enter: 'start', Escape: 'pause', p: 'pause', P: 'pause',
};
addEventListener('keydown', e => {
  if (e.key === 'm' || e.key === 'M') {
    MUTED = !MUTED;
    if (MUTED) { if (music.cur) music[music.cur].pause(); }
    else if (music.cur) music[music.cur].play().catch(() => {});
    return;
  }
  const k = KEYMAP[e.key]; if (!k) return;
  e.preventDefault();
  if (!keys[k]) pressed[k] = true;
  keys[k] = true;
});
addEventListener('keyup', e => { const k = KEYMAP[e.key]; if (k) keys[k] = false; });
// Gamepad (standard mapping): A punch, X kick, B knee, Y spin, RB block, LB clinch, RT flame, LT dodge
const PAD = { 0: 'punch', 2: 'kick', 1: 'knee', 3: 'spin', 5: 'block', 4: 'clinch', 7: 'flame', 6: 'dodge', 9: 'start', 8: 'pause' };
const padPrev = {};
function pollPad() {
  const p = navigator.getGamepads ? navigator.getGamepads()[0] : null;
  if (!p) return;
  const ax = p.axes[0] || 0, ay = p.axes[1] || 0;
  const dl = p.buttons[14]?.pressed, dr = p.buttons[15]?.pressed, du = p.buttons[12]?.pressed, dd = p.buttons[13]?.pressed;
  keys.padLeft = ax < -0.4 || dl; keys.padRight = ax > 0.4 || dr; keys.padUp = ay < -0.4 || du; keys.padDown = ay > 0.4 || dd;
  for (const b in PAD) {
    const on = !!p.buttons[b]?.pressed, k = PAD[b];
    if (on && !padPrev[b]) pressed[k] = true;
    keys['pad_' + k] = on; padPrev[b] = on;
  }
}
const held = k => keys[k] || keys['pad_' + k] ||
  (k === 'left' && keys.padLeft) || (k === 'right' && keys.padRight) || (k === 'up' && keys.padUp) || (k === 'down' && keys.padDown);

// ---------------------------------------------------------------- fighters
// Sprite frame indices on each 4x3 sheet.
const KF = { idle: 0, walkA: 1, walkB: 2, jab: 3, cross: 4, elbow: 5, knee: 6, round: 7, spin: 8, block: 9, hit: 10, down: 11 };
const TF = { idle: 0, walkA: 1, walkB: 2, windup: 3, punch: 4, kick: 5, block: 6, hit: 7, gut: 8, stagger: 9, fall: 10, down: 11 };
const BF = { idle: 0, walkA: 1, walkB: 2, windup: 3, slam: 4, teep: 5, grab: 6, block: 7, hit: 8, kneel: 9, fall: 10, down: 11 };

// Kurt's moveset. Times in seconds. reach = forward distance of the hit box from his center.
const MOVES = {
  jab:   { f: KF.jab,   s: 0.05, a: 0.08, r: 0.12, dmg: 5,  reach: 125, push: 14, sfx: 'punch' },
  cross: { f: KF.cross, s: 0.07, a: 0.08, r: 0.16, dmg: 8,  reach: 140, push: 22, sfx: 'punch' },
  elbow: { f: KF.elbow, s: 0.06, a: 0.08, r: 0.20, dmg: 12, reach: 100, push: 26, sfx: 'hit2', stop: 0.07 },
  knee:  { f: KF.knee,  s: 0.11, a: 0.10, r: 0.30, dmg: 18, reach: 110, push: 60, sfx: 'heavy', down: true, stop: 0.11, fire: true },
  round: { f: KF.round, s: 0.15, a: 0.10, r: 0.30, dmg: 16, reach: 185, push: 70, sfx: 'heavy', down: true, stop: 0.1, lunge: 40 },
  spin:  { f: KF.spin,  s: 0.10, a: 0.08, r: 0.24, dmg: 11, reach: 160, push: 40, sfx: 'hit2', stop: 0.07, spin: true },
  cknee: { f: KF.knee,  s: 0.06, a: 0.06, r: 0.16, dmg: 9,  reach: 90,  push: 0,  sfx: 'hit2', stop: 0.06 },
};
const PUNCH_CHAIN = ['jab', 'cross', 'elbow', 'knee'];

function makeKurt() {
  return { kind: 'kurt', x: 160, y: 600, z: 0, vz: 0, vx: 0, face: 1, hp: 120, maxhp: 120, flame: 0,
    state: 'idle', t: 0, move: null, hitDone: null, chain: 0, chainT: 0, buffer: null, inv: 0,
    flameT: 0, walkT: 0, held: null, grabbedBy: null, mash: 0, h: 300 };
}
function makeThug(x, y, variant = 0) {
  return { kind: 'thug', x, y, z: 0, vz: 0, vx: 0, face: -1, hp: 55, maxhp: 55, state: 'enter', t: 0,
    atk: null, cool: 0.8 + Math.random() * 1.2, slot: Math.random() < 0.5 ? -1 : 1, slotY: (Math.random() - 0.5) * 70,
    walkT: Math.random(), tint: variant, h: [290, 300, 278][variant] || 290, name: 'STREET THUG' };
}
function makeBoss(x, y) {
  return { kind: 'boss', x, y, z: 0, vz: 0, vx: 0, face: -1, hp: 280, maxhp: 280, state: 'enter', t: 0,
    atk: null, cool: 1.4, slot: 1, slotY: 0, walkT: 0, stagger: 0, staggerT: 0, h: 345, name: 'THE BUTCHER — DOORMAN' };
}

// ---------------------------------------------------------------- game state
const G = {
  mode: 'title', t: 0, camX: 0, levelW: 2200, lock: null, kurt: null, foes: [], fx: [], texts: [],
  wave: 0, combo: 0, comboT: 0, shake: 0, hitstop: 0, flash: 0, banner: null, focus: null, endT: 0,
};
const WAVES = [
  { trigger: 240, lock: [0, 1280], spawn: () => [makeThug(1350, 560), makeThug(-120, 650, 1)] },
  { trigger: 640, lock: [380, 1660], spawn: () => [makeThug(1760, 540), makeThug(1800, 650, 1), makeThug(280, 600, 2)] },
  { trigger: 1050, lock: [820, 2100], boss: true, spawn: () => [makeBoss(2200, 600)] },
];

function reset() {
  G.kurt = makeKurt(); G.foes = []; G.fx = []; G.texts = []; G.wave = 0; G.lock = null;
  G.combo = 0; G.camX = 0; G.banner = { text: 'CHAPTER 1 — THE DOOR', sub: 'Find the man who runs the underground. Get in.', t: 3 };
  G.mode = 'play'; G.endT = 0; playMusic('fight');
}

// ---------------------------------------------------------------- helpers
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const rnd = (a, b) => a + Math.random() * (b - a);
function frameOf(e) {
  return e.kind === 'kurt' ? 'kurt' : e.kind === 'thug' ? 'thug' : 'boss';
}
function spark(x, y, big, color) {
  const n = big ? 16 : 8;
  for (let i = 0; i < n; i++) {
    const a = rnd(0, Math.PI * 2), s = rnd(500, big ? 1500 : 1000);
    G.fx.push({ x, y, vx: Math.cos(a) * s, vy: Math.sin(a) * s, life: rnd(0.08, 0.2), max: 0.2, c: color || '#ffe2a0', r: rnd(3, big ? 7 : 5) });
  }
  G.fx.push({ star: true, x, y, life: big ? 0.16 : 0.11, max: big ? 0.16 : 0.11, r: big ? 95 : 55, n: big ? 9 : 7, rot: rnd(0, 6),
    j: Array.from({ length: 6 }, () => Math.random()), c: color || '#ff9a2a' });
  G.fx.push({ ring: true, x, y, life: 0.16, max: 0.16, r: big ? 80 : 46, c: color || '#fff2c4' });
}
function popText(x, y, s, c = '#fff') { G.texts.push({ x, y, s, c, life: 0.8 }); }
function inLane(a, b, tol = 34) { return Math.abs(a.y - b.y) < tol; }

// ---------------------------------------------------------------- Kurt
function kurtUpdate(k, dt) {
  k.t += dt; k.inv = Math.max(0, k.inv - dt); k.chainT -= dt;
  if (k.flameT > 0) { k.flameT -= dt; if (k.flameT <= 0) popText(k.x, k.y - 330, 'FLAME FADES', '#9cc8ff'); }
  const speedMul = k.flameT > 0 ? 1.2 : 1;

  // buffered inputs so combos feel responsive
  for (const b of ['punch', 'kick', 'knee', 'spin', 'clinch', 'dodge']) if (pressed[b]) k.buffer = { b, t: 0.25 };
  if (k.buffer) { k.buffer.t -= dt; if (k.buffer.t <= 0) k.buffer = null; }

  if (pressed.flame && k.flame >= 100 && k.flameT <= 0) {
    k.flame = 0; k.flameT = 9; sfx('flame', 0.9); G.flash = 0.35; G.shake = 0.4;
    G.banner = { text: 'BLUE FLAME', sub: 'Wrath burns hotter — and shorter.', t: 1.6, blue: true };
    for (let i = 0; i < 40; i++) G.fx.push({ x: k.x + rnd(-60, 60), y: k.y - rnd(0, 300), vx: rnd(-80, 80), vy: rnd(-500, -150), life: rnd(0.4, 0.9), max: 0.9, c: '#62b4ff', r: rnd(3, 8) });
  }

  switch (k.state) {
    case 'grabbed': {
      // mash punch to break the Butcher's grip
      if (pressed.punch || pressed.kick || pressed.knee) { k.mash++; G.shake = 0.08; }
      if (k.mash >= 8) { breakGrab(k); }
      return;
    }
    case 'hit': if (k.t > 0.28) setK(k, 'idle'); return;
    case 'down':
      if (k.t > 1.1) { setK(k, 'idle'); k.inv = 1.0; }
      return;
    case 'dead': return;
    case 'dodge': {
      k.x += k.vx * dt; k.y = clamp(k.y + (k.vy || 0) * dt, FLOOR_TOP, FLOOR_BOT);
      if (k.t > 0.28) setK(k, 'idle');
      return;
    }
    case 'clinch': {
      const f = k.held;
      if (!f || f.state !== 'held') { k.held = null; setK(k, 'idle'); return; }
      f.x = k.x + k.face * 70; f.y = k.y + 2; f.face = -k.face;
      if (k.move) { attackTick(k, dt); return; }
      if (k.buffer && (k.buffer.b === 'knee' || k.buffer.b === 'punch')) {
        k.buffer = null; k.clinchHits = (k.clinchHits || 0) + 1;
        startMove(k, k.clinchHits >= 3 ? 'knee' : 'cknee');   // 3rd knee launches him out of the clinch
        return;
      }
      if (k.t > 1.6) { f.state = 'hit'; f.t = 0; k.held = null; setK(k, 'idle'); }
      return;
    }
    case 'attack': attackTick(k, dt); return;
  }

  // free movement
  let mx = 0, my = 0;
  if (held('left')) mx -= 1; if (held('right')) mx += 1;
  if (held('up')) my -= 1; if (held('down')) my += 1;
  const blocking = held('block');
  if (blocking) { k.state = 'block'; mx = 0; my = 0; } else if (k.state === 'block') k.state = 'idle';
  if (mx) k.face = mx;
  const sp = 300 * speedMul;
  k.x += mx * sp * dt; k.y = clamp(k.y + my * sp * 0.6 * dt, FLOOR_TOP, FLOOR_BOT);
  if (!blocking) k.state = (mx || my) ? 'walk' : 'idle';
  if (mx || my) k.walkT += dt * 6.5 * speedMul;

  if (k.buffer && !blocking) {
    const b = k.buffer.b; k.buffer = null;
    if (b === 'punch') {
      if (k.chainT <= 0) k.chain = 0;
      startMove(k, PUNCH_CHAIN[k.chain]); k.chain = (k.chain + 1) % PUNCH_CHAIN.length; k.chainT = 0.55;
    } else if (b === 'kick') { startMove(k, 'round'); k.chain = 0; }
    else if (b === 'knee') { startMove(k, 'knee'); k.chain = 0; }
    else if (b === 'spin') { startMove(k, 'spin'); k.chain = 0; }
    else if (b === 'clinch') tryClinch(k);
    else if (b === 'dodge') {
      setK(k, 'dodge'); k.inv = 0.3; sfx('whoosh', 0.5);
      k.vx = (mx || -k.face) * 520; k.vy = my * 380;
    }
  }
}
function setK(k, s) { k.state = s; k.t = 0; k.move = null; }
function startMove(k, name) {
  const m = MOVES[name];
  if (m.spin) k.face = nearestFoeDir(k) || k.face;
  else { const d = nearestFoeDir(k, 260); if (d) k.face = d; }
  if (k.state !== 'clinch') k.state = 'attack';
  k.t = 0; k.move = name; k.hitDone = new Set(); k.lunged = false;
  sfx('whoosh', 0.35);
}
function nearestFoeDir(k, maxD = 9999) {
  let best = null, bd = maxD;
  for (const f of G.foes) {
    if (f.state === 'dead' || f.state === 'down') continue;
    const d = Math.abs(f.x - k.x) + Math.abs(f.y - k.y) * 2;
    if (d < bd) { bd = d; best = f; }
  }
  return best ? Math.sign(best.x - k.x) || k.face : 0;
}
function attackTick(k, dt) {
  k.t += 0; // time already advanced in kurtUpdate
  const m = MOVES[k.move], spd = k.flameT > 0 ? 1.15 : 1, t = k.t * spd;
  if (m.lunge && !k.lunged && t > m.s) { k.lunged = true; k.x += k.face * m.lunge; }
  if (t >= m.s && t <= m.s + m.a) {
    for (const f of G.foes) {
      if (k.hitDone.has(f) || f.state === 'dead' || f.state === 'down' || f.state === 'enter') continue;
      if (k.state !== 'clinch' && f.state === 'held') continue;
      const dx = (f.x - k.x) * k.face;
      if (dx > -20 && dx < m.reach + 30 && inLane(k, f)) { k.hitDone.add(f); landHit(k, f, m); }
    }
  }
  if (t > m.s + m.a + m.r) {
    k.move = null;
    if (k.state !== 'clinch') k.state = 'idle';
  }
}
function landHit(k, f, m) {
  const flame = k.flameT > 0;
  let dmg = m.dmg * (flame ? 1.8 : 1);
  const front = Math.sign(k.x - f.x) === f.face;
  // thugs can block light hits from the front
  if (f.kind === 'thug' && front && f.state === 'block') {
    dmg = 0; spark((k.x + f.x) / 2, f.y - 200, false, '#bcd'); sfx('hit2', 0.4);
    popText(f.x, f.y - 300, 'BLOCKED', '#bcd'); return;
  }
  f.hp -= dmg;
  G.combo++; G.comboT = 1.4;
  k.flame = Math.min(100, k.flame + (flame ? 0 : 7));
  G.hitstop = m.stop || 0.045;
  const big = !!m.down;
  spark(f.x - k.face * 10, f.y - (m.f === KF.round ? 260 : 210), big, flame ? '#7cc4ff' : (m.fire ? '#ff9b3d' : null));
  sfx(m.sfx, big ? 0.9 : 0.7);
  if (big) { G.shake = 0.25; G.impact = 0.075; G.impactAt = { x: f.x, y: f.y - 200 }; G.zoom = 1.07; }
  else G.zoom = Math.max(G.zoom || 1, 1.02);
  f.hitFlash = 0.08;
  if (f.kind === 'boss') bossTakeHit(f, dmg, m, k);
  else {
    f.x += k.face * (m.push + (flame ? 40 : 0));
    if (f.hp <= 0) { knockDown(f, k.face, true); }
    else if (m.down || flame && m.dmg >= 8) knockDown(f, k.face, false);
    else if (f.state !== 'held') { f.state = 'hit'; f.t = 0; f.hitF = m.f === KF.elbow || m.f === KF.knee ? TF.gut : TF.hit; f.atk = null; }
  }
  G.focus = { f, t: 3 };
}
function knockDown(f, dir, dead) {
  f.state = 'airborne'; f.t = 0; f.vz = 520; f.z = 0.01; f.vx = dir * 260; f.atk = null; f.dead = dead;
}
function tryClinch(k) {
  for (const f of G.foes) {
    if (f.kind !== 'thug' || !['idle', 'walk', 'hit', 'windup', 'recover', 'block'].includes(f.state)) continue;
    if (Math.abs(f.x - k.x) < 120 && inLane(k, f, 40)) {
      k.face = Math.sign(f.x - k.x) || k.face; f.state = 'held'; f.atk = null; f.t = 0;
      k.state = 'clinch'; k.t = 0; k.held = f; k.clinchHits = 0; k.move = null;
      popText(k.x, k.y - 330, 'CLINCH — KNEE! (L)', '#ffd27a'); sfx('hit2', 0.4);
      return;
    }
  }
}
function hurtKurt(k, dmg, from, opts = {}) {
  if (k.inv > 0 || k.state === 'down' || k.state === 'dead' || k.state === 'grabbed') return false;
  const front = Math.sign(from.x - k.x) === k.face;
  if (k.state === 'block' && front && !opts.unblockable) {
    k.x -= k.face * 18; spark((k.x + from.x) / 2, k.y - 200, false, '#9fd'); sfx('hit2', 0.4);
    k.flame = Math.min(100, k.flame + 3); return false;
  }
  k.hp -= dmg; k.flame = Math.min(100, k.flame + 5); G.combo = 0;
  spark(k.x, k.y - 210, dmg > 12, '#ff6a5a'); sfx(dmg > 12 ? 'heavy' : 'punch', 0.8);
  G.shake = dmg > 12 ? 0.3 : 0.12; G.flash = 0.12;
  if (k.held) { k.held.state = 'hit'; k.held.t = 0; k.held = null; }
  if (k.hp <= 0) { k.hp = 0; k.state = 'dead'; k.t = 0; k.move = null; return true; }
  if (opts.down) { k.state = 'down'; k.t = 0; k.move = null; k.x -= k.face * 60; }
  else { k.state = 'hit'; k.t = 0; k.move = null; k.x -= k.face * 26; }
  return true;
}
function breakGrab(k) {
  const b = k.grabbedBy; k.grabbedBy = null; k.mash = 0;
  setK(k, 'idle'); k.inv = 0.6; k.x -= k.face * 40;
  if (b) { b.state = 'recover'; b.t = 0; b.atk = null; b.hp -= 10; spark(b.x, b.y - 260, true); sfx('heavy', 0.8); popText(b.x, b.y - 360, 'BROKE FREE', '#ffd27a'); }
}

// ---------------------------------------------------------------- enemies
let attackTokens = 0;
function foeUpdate(f, dt, k) {
  f.t += dt;
  switch (f.state) {
    case 'enter': {
      const tx = clamp(f.x, G.camX + 80, G.camX + W - 80);
      f.x += Math.sign(tx - f.x) * 220 * dt; f.walkT += dt * 6;
      f.face = Math.sign(k.x - f.x) || f.face;
      if (Math.abs(tx - f.x) < 6) { f.state = 'idle'; f.t = 0; }
      return;
    }
    case 'held': return;
    case 'hit': if (f.t > (f.kind === 'boss' ? 0.3 : 0.38)) { f.state = 'idle'; f.t = 0; f.cool = rnd(0.4, 0.9); } return;
    case 'airborne': {
      f.vz -= 1700 * dt; f.z += f.vz * dt; f.x += f.vx * dt;
      if (f.z <= 0) { f.z = 0; f.state = f.dead ? 'dead' : 'down'; f.t = 0; G.shake = Math.max(G.shake, 0.15); sfx('hit2', 0.5);
        for (let i = 0; i < 10; i++) G.fx.push({ dust: true, x: f.x + rnd(-90, 90), y: f.y - rnd(0, 20), vx: rnd(-60, 60), vy: rnd(-40, -10), life: rnd(0.4, 0.8), max: 0.8, r: rnd(14, 30) }); }
      return;
    }
    case 'down': if (f.t > 1.2) { f.state = 'idle'; f.t = 0; f.cool = rnd(0.5, 1); } return;
    case 'dead': return;
    case 'kneel': if (f.t > 1.4) { f.state = 'idle'; f.t = 0; f.cool = 0.6; } return;
    case 'windup': case 'attack': case 'recover': enemyAttackTick(f, dt, k); return;
    case 'block': if (f.t > 0.5) { f.state = 'idle'; f.t = 0; } return;
  }
  // idle / walk: move to a slot beside Kurt, then attack when the cooldown is up
  f.face = Math.sign(k.x - f.x) || f.face;
  f.cool -= dt;
  const want = f.kind === 'boss' ? 140 : 112;   // striking distance; must sit inside the shortest attack reach
  if (Math.abs(k.x - f.x) > 260 || f.cool > 0.4) f.slot = Math.sign(f.x - k.x) || f.slot;
  const tx = k.x + f.slot * (f.cool > 0 ? want + 60 : want - 10), ty = clamp(k.y + (f.cool > 0 ? f.slotY : 0), FLOOR_TOP, FLOOR_BOT);
  const dx = tx - f.x, dy = ty - f.y, sp = f.kind === 'boss' ? 120 : 175;
  if (Math.abs(dx) > 8 || Math.abs(dy) > 6) {
    f.x += clamp(dx, -sp * dt, sp * dt); f.y += clamp(dy, -sp * 0.6 * dt, sp * 0.6 * dt);
    f.state = 'walk'; f.walkT += dt * (f.kind === 'boss' ? 4 : 6);
  } else f.state = 'idle';
  // thugs sometimes block when Kurt winds up in front of them
  if (f.kind === 'thug' && k.state === 'attack' && k.t < 0.05 && Math.abs(k.x - f.x) < 200 && inLane(k, f) && Math.random() < 0.18) {
    f.state = 'block'; f.t = 0; return;
  }
  const close = Math.abs(k.x - f.x) < want + 12 && inLane(k, f, 40);
  if (f.cool <= 0 && close && k.state !== 'dead' && (attackTokens < 2 || f.kind === 'boss')) startEnemyAttack(f, k);
}
const THUG_ATK = {
  punch: { wind: 0.42, act: 0.1, rec: 0.4, dmg: 8, reach: 135, f1: TF.windup, f2: TF.punch },
  kick:  { wind: 0.5,  act: 0.1, rec: 0.45, dmg: 10, reach: 165, f1: TF.windup, f2: TF.kick },
};
const BOSS_ATK = {
  slam: { wind: 0.7, act: 0.12, rec: 0.6, dmg: 22, reach: 165, f1: BF.windup, f2: BF.slam, down: true },
  teep: { wind: 0.45, act: 0.1, rec: 0.45, dmg: 12, reach: 190, f1: BF.idle, f2: BF.teep, push: 140 },
  grab: { wind: 0.55, act: 0.14, rec: 0.7, dmg: 0, reach: 140, f1: BF.idle, f2: BF.grab, grab: true },
};
function startEnemyAttack(f, k) {
  let name;
  if (f.kind === 'boss') {
    const r = Math.random();
    name = r < 0.42 ? 'slam' : r < 0.75 ? 'teep' : 'grab';
    f.atk = { name, ...BOSS_ATK[name] };
  } else {
    name = Math.random() < 0.6 ? 'punch' : 'kick';
    f.atk = { name, ...THUG_ATK[name] };
  }
  f.state = 'windup'; f.t = 0; f.face = Math.sign(k.x - f.x) || f.face; f.hasHit = false; attackTokens++;
}
function enemyAttackTick(f, dt, k) {
  const a = f.atk; if (!a) { f.state = 'idle'; return; }
  if (f.state === 'windup' && f.t >= a.wind) { f.state = 'attack'; f.t = 0; sfx('whoosh', 0.35); if (a.grab) f.x += f.face * 30; }
  else if (f.state === 'attack') {
    if (!f.hasHit && inLane(f, k, 38)) {
      const dx = (k.x - f.x) * f.face;
      if (dx > -10 && dx < a.reach) {
        f.hasHit = true;
        if (a.grab) {
          if (k.inv <= 0 && !['down', 'dead', 'dodge'].includes(k.state)) {
            k.state = 'grabbed'; k.t = 0; k.move = null; k.grabbedBy = f; k.mash = 0; k.face = -f.face;
            f.state = 'grabbing'; f.t = 0; popText(k.x, k.y - 340, 'MASH J TO BREAK FREE!', '#ff8a7a');
            if (k.held) { k.held.state = 'hit'; k.held = null; }
            return;
          }
        } else {
          const landed = hurtKurt(k, a.dmg, f, { down: a.down });
          if (landed && a.push) k.x += f.face * a.push;
        }
      }
    }
    if (f.t >= a.act) { f.state = 'recover'; f.t = 0; }
  } else if (f.state === 'recover' && f.t >= a.rec) {
    f.state = 'idle'; f.t = 0; f.atk = null; attackTokens = Math.max(0, attackTokens - 1);
    f.cool = f.kind === 'boss' ? rnd(0.9, 1.6) : rnd(1.1, 2.4);
  }
}
function grabTick(f, dt, k) {
  f.t += dt;
  k.x = f.x + f.face * 95; k.y = f.y + 1;
  if (k.state !== 'grabbed') { f.state = 'recover'; f.t = 0; return; }
  f.squeeze = (f.squeeze || 0) + dt;
  if (f.squeeze > 0.6) {
    f.squeeze = 0; k.hp -= 6; G.shake = 0.15; sfx('punch', 0.6); spark(k.x, k.y - 230, false, '#ff6a5a');
    if (k.hp <= 0) { k.hp = 0; k.state = 'dead'; k.t = 0; }
  }
  if (f.t > 2.6 && k.state === 'grabbed') {   // throw him
    k.grabbedBy = null; k.mash = 0; k.state = 'down'; k.t = 0; k.x += f.face * 160; k.hp -= 8;
    G.shake = 0.35; sfx('heavy', 0.9); f.state = 'recover'; f.t = 0;
  }
}
function bossTakeHit(b, dmg, m, k) {
  b.stagger += dmg; b.staggerT = 2.2;
  const armored = (b.state === 'windup' || b.state === 'attack') && dmg < 14 && k.flameT <= 0;
  if (b.hp <= 0) { b.hp = 0; knockDown(b, k.face, true); attackTokens = Math.max(0, attackTokens - (b.atk ? 1 : 0)); b.atk = null; return; }
  if (b.stagger >= 55) {
    b.stagger = 0; b.state = 'kneel'; b.t = 0; b.atk = null; attackTokens = Math.max(0, attackTokens - 1);
    popText(b.x, b.y - 380, 'STAGGERED!', '#ffd27a'); G.shake = 0.3; return;
  }
  if (armored) { popText(b.x, b.y - 380, 'ARMOR', '#bbb'); return; }
  if (m.down && b.state !== 'kneel') { b.x += k.face * 30; }
  if (b.state !== 'kneel') {
    if (b.atk) attackTokens = Math.max(0, attackTokens - 1);
    b.state = 'hit'; b.t = 0; b.atk = null; b.x += k.face * 12;
  }
}

// ---------------------------------------------------------------- world
function update(dt) {
  pollPad();
  if (G.mode === 'title') { if (pressed.start || pressed.punch) reset(); return; }
  if (G.mode === 'over' || G.mode === 'win') { G.endT += dt; if (G.endT > 1.2 && (pressed.start || pressed.punch)) reset(); return; }
  if (pressed.pause) { G.mode = G.mode === 'pause' ? 'play' : 'pause'; }
  if (G.mode === 'pause') return;

  if (G.hitstop > 0) { G.hitstop -= dt; return false; }   // keep presses queued through hit-freeze
  G.t += dt;
  const k = G.kurt;
  kurtUpdate(k, dt);
  attackTokens = G.foes.filter(f => f.atk && ['windup', 'attack', 'recover'].includes(f.state)).length;
  for (const f of G.foes) { if (f.state === 'grabbing') grabTick(f, dt, k); else foeUpdate(f, dt, k); }
  // push standing enemies apart so they don't stack on one spot
  const standing = G.foes.filter(f => ['idle', 'walk', 'windup', 'recover', 'block', 'hit'].includes(f.state));
  for (let i = 0; i < standing.length; i++) for (let j = i + 1; j < standing.length; j++) {
    const a = standing[i], b = standing[j], dx = b.x - a.x, dy = b.y - a.y;
    if (Math.abs(dx) < 110 && Math.abs(dy) < 40) {
      const p = (110 - Math.abs(dx)) * 0.5 * Math.min(1, dt * 8) * (dx >= 0 ? 1 : -1);
      a.x -= p; b.x += p;
      if (Math.abs(dy) < 20) { a.y -= 30 * dt; b.y += 30 * dt; }
    }
  }

  // keep everyone in bounds and in the lock zone
  const lo = G.lock ? G.lock[0] + 50 : 40, hi = G.lock ? G.lock[1] - 50 : G.levelW - 40;
  k.x = clamp(k.x, lo, hi);
  for (const f of G.foes) if (f.state !== 'enter' && f.state !== 'dead') { f.x = clamp(f.x, lo - 20, hi + 20); f.y = clamp(f.y, FLOOR_TOP, FLOOR_BOT); }

  // waves
  const w = WAVES[G.wave];
  if (!G.lock && w && k.x > w.trigger) {
    G.lock = w.lock; G.foes.push(...w.spawn());
    if (w.boss) {
      playMusic('boss');
      G.banner = { text: 'THE BUTCHER', sub: 'He decides who walks through that door.', t: 2.6 }; G.cine = 2.8;
    } else G.banner = { text: 'FIGHT!', t: 1 };
  }
  if (G.lock && G.foes.every(f => f.state === 'dead')) {
    const wasBoss = WAVES[G.wave].boss;
    G.lock = null; G.wave++; G.foes = G.foes.filter(f => f.t < 2.5);
    if (wasBoss) { G.mode = 'win'; G.endT = 0; playMusic(null); sfx('cheer', 0.8); }
    else G.banner = { text: 'GO →', t: 1.2 };
  }
  if (k.state === 'dead' && k.t > 1.6) { G.mode = 'over'; G.endT = 0; playMusic(null); }
  if (k.state === 'dead') k.t += 0;

  // camera
  const lo2 = G.lock ? G.lock[0] : 0, hi2 = G.lock ? G.lock[1] - W : G.levelW - W;
  const target = clamp(k.x - W * 0.42, lo2, Math.max(lo2, hi2));
  G.camX += (target - G.camX) * Math.min(1, dt * 6);

  // fx
  for (const p of G.fx) { p.life -= dt; if (!p.ring && !p.star) { p.x += p.vx * dt; p.y += p.vy * dt; if (!p.dust && !p.flame) p.vy += 1400 * dt; } }
  G.fx = G.fx.filter(p => p.life > 0);
  for (const t of G.texts) { t.life -= dt; t.y -= 50 * dt; }
  G.texts = G.texts.filter(t => t.life > 0);
  if (G.comboT > 0) { G.comboT -= dt; if (G.comboT <= 0) G.combo = 0; }
  if (G.banner) { G.banner.t -= dt; if (G.banner.t <= 0) G.banner = null; }
  if (G.focus) { G.focus.t -= dt; if (G.focus.t <= 0 || G.focus.f.state === 'dead') G.focus = null; }
  G.shake = Math.max(0, G.shake - dt); G.flash = Math.max(0, G.flash - dt);
}

// ---------------------------------------------------------------- render
// Anime-look renderer: multi-frame painted sprites lit by the alley's lights, wet-street
// reflections, rain/steam, ink hit-sparks, impact frames, grade + grain.
const FR = window.FRAMES || {};
const FIMG = {};
for (const n in FR) FR[n].frames.forEach(f => { FIMG[f.f] = loadImg('assets/frames/' + f.f); });
const PORTRAIT = { kurt: loadImg('assets/portrait_kurt.jpg'), boss: loadImg('assets/portrait_boss.jpg') };

// which strip + frame a fighter shows right now
const KSTRIP = { jab: 'kurt_jab', cross: 'kurt_cross', elbow: 'kurt_elbow', knee: 'kurt_knee', cknee: 'kurt_knee', round: 'kurt_round', spin: 'kurt_spin' };
function phase3(t, s, a, r) { return t < s ? 0 : t < s + a + r * 0.45 ? 1 : 2; }
// frame order per strip when it differs from wind-up / strike / recover
const ORDER = { kurt_spin: [1, 2, 0] };
// standing height (px) for strips whose first frame isn't an upright stance
const REF = { kurt_react: 450, boss_react: 470, boss_slam: 638 };
function anim(e) {
  const cyc = (strip, rate, t) => [strip, Math.floor(t * rate) % ((FR[strip] && FR[strip].frames.length) || 1)];
  if (e.kind === 'kurt') {
    switch (e.state) {
      case 'walk': return cyc('kurt_walk', 1.05, e.walkT);
      case 'attack': { const m = MOVES[e.move]; if (!m) return ['kurt_idle', 0];
        const st = KSTRIP[e.move], ph = phase3(e.t * (e.flameT > 0 ? 1.15 : 1), m.s, m.a, m.r); return [st, ORDER[st] ? ORDER[st][ph] : ph]; }
      case 'clinch': { if (!e.move) return ['kurt_knee', 0]; const m = MOVES[e.move]; return ['kurt_knee', phase3(e.t, m.s, m.a, m.r)]; }
      case 'block': return ['kurt_react', 0];
      case 'hit': case 'grabbed': return ['kurt_react', 1];
      case 'down': return ['kurt_react', e.t < 0.22 ? 2 : 3];
      case 'dead': return ['kurt_react', e.t < 0.22 ? 2 : 3];
      case 'dodge': return ['kurt_walk', 1];
      default: return cyc('kurt_idle', 5, G.t + (e.seed || 0));
    }
  }
  const P = e.kind;  // 'thug' | 'boss'
  const atkStrip = a => a.name === 'punch' ? 'thug_punch' : a.name === 'kick' ? 'thug_kick' : a.name === 'slam' ? 'boss_slam' : a.name === 'teep' ? 'boss_teep' : null;
  switch (e.state) {
    case 'walk': case 'enter': return cyc(P + '_walk', 1.0, e.walkT);
    case 'windup': return e.atk && atkStrip(e.atk) ? [atkStrip(e.atk), 0] : [P + '_walk', 0];
    case 'attack': return e.atk && atkStrip(e.atk) ? [atkStrip(e.atk), 1] : ['@boss', BF.grab];
    case 'grabbing': return ['@boss', BF.grab];
    case 'recover': return e.atk && atkStrip(e.atk) ? [atkStrip(e.atk), e.t < 0.12 ? 1 : 2] : [P + '_walk', 0];
    case 'hit': return [P + '_react', P === 'thug' && e.hitF === TF.gut ? 1 : 0];
    case 'held': return ['thug_react', 1];
    case 'block': return P === 'thug' ? ['@thug', TF.block] : ['@boss', BF.block];
    case 'airborne': return [P + '_react', 2];
    case 'down': case 'dead': return [P + '_react', 3];
    case 'kneel': return ['boss_react', 1];
    default: return [P + '_walk', 0];
  }
}
// original single-pose 4x3 sheets (fallback when a strip frame is missing)
const FRAME_FLIP = { kurt: { 11: true }, boss: { 9: true } };
function spriteFor(e) {
  if (e.kind === 'kurt') {
    switch (e.state) {
      case 'walk': return Math.floor(e.walkT) % 2 ? KF.walkA : KF.walkB;
      case 'attack': return MOVES[e.move] ? MOVES[e.move].f : KF.idle;
      case 'block': return KF.block;
      case 'hit': case 'grabbed': return KF.hit;
      case 'down': case 'dead': return KF.down;
      default: return KF.idle;
    }
  }
  const F = e.kind === 'boss' ? BF : TF;
  switch (e.state) {
    case 'walk': case 'enter': return Math.floor(e.walkT) % 2 ? F.walkA : F.walkB;
    case 'airborne': return F.fall;
    case 'down': case 'dead': return F.down;
    default: return F.idle;
  }
}
// resolve to an image + frame record + scale; '@sheet' means a frame from the original 4x3 sheet
function frameRec(e) {
  let [strip, i] = anim(e);
  if (strip && strip[0] !== '@' && FR[strip]) {
    const S = FR[strip], fr = S.frames[Math.min(i, S.frames.length - 1)], img = FIMG[fr.f];
    if (img && img.complete && img.naturalWidth) return { img, fr, s: e.h / (REF[strip] || S.ref), flip: 1 };
  }
  // fallback: the original single-pose sheet
  const sheet = strip && strip[0] === '@' ? strip.slice(1) : e.kind, frames = META[sheet];
  if (!frames) return null;
  if (!(strip && strip[0] === '@')) i = spriteFor(e);
  const fr = frames[i] || frames[0], img = IMG[fr.f];
  if (!img || !img.complete || !img.naturalWidth) return null;
  return { img, fr, s: e.h / frames[0].fh, flip: FRAME_FLIP[sheet] && FRAME_FLIP[sheet][i] ? -1 : 1 };
}

// scene lights (x as a fraction of the level width), sampled per fighter for rim + tint
const LIGHTS = [[0.27, [255, 160, 80], 560], [0.47, [255, 80, 170], 380], [0.89, [255, 50, 36], 560]];
function lightAt(x) {
  let best = null, bw = 0;
  for (const [fx, c, r] of LIGHTS) { const lx = fx * G.levelW, w = Math.max(0, 1 - Math.abs(lx - x) / r); if (w > bw) { bw = w; best = { x: lx, c }; } }
  return best ? { ...best, w: bw } : null;
}
const OC = document.createElement('canvas'), octx = OC.getContext('2d');
function litSprite(e, rec, mode) {
  const { img, fr } = rec;
  OC.width = fr.w; OC.height = fr.h;
  octx.globalCompositeOperation = 'source-over'; octx.drawImage(img, 0, 0);
  octx.globalCompositeOperation = 'source-atop';
  if (mode === 'silhouette') { octx.fillStyle = '#0b0a0a'; octx.fillRect(0, 0, fr.w, fr.h); return OC; }
  // night ambient: cool shadow over everything
  octx.fillStyle = 'rgba(16,26,58,0.24)'; octx.fillRect(0, 0, fr.w, fr.h);
  // rim light from the nearest practical light
  const L = lightAt(e.x);
  if (L) {
    const flip = (e.face === 1 ? 1 : -1) * rec.flip;
    const fromRight = (L.x > e.x ? 1 : -1) * flip > 0;
    const g = octx.createLinearGradient(fromRight ? fr.w : 0, 0, fromRight ? fr.w * 0.55 : fr.w * 0.45, 0);
    g.addColorStop(0, `rgba(${L.c},${0.55 * L.w})`); g.addColorStop(1, `rgba(${L.c},0)`);
    octx.fillStyle = g; octx.fillRect(0, 0, fr.w, fr.h);
  }
  // moonlight from above
  const mg = octx.createLinearGradient(0, 0, 0, fr.h * 0.5);
  mg.addColorStop(0, 'rgba(150,175,255,0.16)'); mg.addColorStop(1, 'rgba(150,175,255,0)');
  octx.fillStyle = mg; octx.fillRect(0, 0, fr.w, fr.h);
  if (e.kind === 'kurt' && e.flameT > 0) { octx.fillStyle = `rgba(70,160,255,${0.12 + Math.sin(G.t * 18) * 0.05})`; octx.fillRect(0, 0, fr.w, fr.h); }
  if (mode === 'tele') { octx.fillStyle = 'rgba(255,40,20,0.38)'; octx.fillRect(0, 0, fr.w, fr.h); }
  if (e.hitFlash > 0) { octx.fillStyle = `rgba(255,255,255,${e.hitFlash * 3})`; octx.fillRect(0, 0, fr.w, fr.h); }
  if (e.kind === 'thug' && e.tint) { octx.fillStyle = e.tint === 1 ? 'rgba(0,0,0,0.18)' : 'rgba(80,50,20,0.15)'; octx.fillRect(0, 0, fr.w, fr.h); }
  return OC;
}

// ------------------------------------------------ background + atmosphere
const rain = Array.from({ length: 260 }, () => ({ x: Math.random() * W, y: Math.random() * H, v: 900 + Math.random() * 500, l: 14 + Math.random() * 18, z: Math.random() }));
const splashes = [];
const NOISE = (() => { const c = document.createElement('canvas'); c.width = c.height = 256; const g = c.getContext('2d'), d = g.createImageData(256, 256);
  for (let i = 0; i < d.data.length; i += 4) { const v = Math.random() * 255; d.data[i] = d.data[i + 1] = d.data[i + 2] = v; d.data[i + 3] = 255; } g.putImageData(d, 0, 0); return c; })();
let grainPat = null;

function drawBG() {
  ctx.fillStyle = '#0d0a0a'; ctx.fillRect(0, 0, W, H);
  if (BG.complete && BG.naturalWidth) {
    const s = BG_H / BG.naturalHeight, bw = BG.naturalWidth * s;
    G.levelW = Math.max(bw, W);
    ctx.drawImage(BG, -G.camX, H - BG_H, bw, BG_H);
  }
  // pools of light spilling onto the wet street
  ctx.save(); ctx.globalCompositeOperation = 'lighter';
  for (const [fx, c, r] of LIGHTS) {
    const x = fx * G.levelW - G.camX; if (x < -r || x > W + r) continue;
    const g = ctx.createRadialGradient(x, FLOOR_TOP + 60, 10, x, FLOOR_TOP + 60, r * 0.75);
    g.addColorStop(0, `rgba(${c},0.22)`); g.addColorStop(1, `rgba(${c},0)`);
    ctx.fillStyle = g; ctx.beginPath(); ctx.ellipse(x, FLOOR_TOP + 70, r * 0.8, 110, 0, 0, Math.PI * 2); ctx.fill();
  }
  ctx.restore();
  // low haze over the street
  const hz = ctx.createLinearGradient(0, FLOOR_TOP - 140, 0, FLOOR_TOP + 40);
  hz.addColorStop(0, 'rgba(120,140,190,0)'); hz.addColorStop(0.6, 'rgba(120,140,190,0.10)'); hz.addColorStop(1, 'rgba(120,140,190,0)');
  ctx.fillStyle = hz; ctx.fillRect(0, FLOOR_TOP - 140, W, 180);
}
function drawSteam() {
  ctx.save(); ctx.globalCompositeOperation = 'screen';
  for (const fx of [0.31, 0.6]) {
    const bx = fx * G.levelW - G.camX; if (bx < -200 || bx > W + 200) continue;
    for (let i = 0; i < 7; i++) {
      const k = ((G.t * 0.22 + i / 7) % 1), x = bx + Math.sin(i * 2.3 + G.t) * 20 + k * 60, y = FLOOR_TOP - 40 - k * 260, r = 30 + k * 110;
      const g = ctx.createRadialGradient(x, y, 0, x, y, r); g.addColorStop(0, `rgba(200,190,185,${0.10 * Math.sin(k * Math.PI)})`); g.addColorStop(1, 'rgba(200,190,185,0)');
      ctx.fillStyle = g; ctx.fillRect(x - r, y - r, r * 2, r * 2);
    }
  }
  ctx.restore();
}
function drawRain(dt) {
  ctx.save(); ctx.lineCap = 'round';
  for (const d of rain) {
    d.y += d.v * dt; d.x -= d.v * 0.18 * dt;
    if (d.y > FLOOR_TOP + (d.z * (FLOOR_BOT - FLOOR_TOP))) {
      if (Math.random() < 0.5) splashes.push({ x: d.x, y: d.y, t: 0 });
      d.y = -20 - Math.random() * 200; d.x = Math.random() * (W + 200);
    }
    ctx.strokeStyle = `rgba(170,190,235,${0.12 + d.z * 0.22})`; ctx.lineWidth = 0.8 + d.z;
    ctx.beginPath(); ctx.moveTo(d.x, d.y); ctx.lineTo(d.x + d.l * 0.18, d.y - d.l); ctx.stroke();
  }
  for (let i = splashes.length - 1; i >= 0; i--) {
    const s = splashes[i]; s.t += dt; if (s.t > 0.25) { splashes.splice(i, 1); continue; }
    ctx.strokeStyle = `rgba(190,205,240,${0.35 * (1 - s.t / 0.25)})`; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.ellipse(s.x, s.y, 3 + s.t * 40, 1 + s.t * 8, 0, 0, Math.PI * 2); ctx.stroke();
  }
  ctx.restore();
}
// foreground: hanging wires and string lights, moving faster than the street (parallax)
function drawForeground() {
  const px = G.camX * 1.25;
  ctx.save(); ctx.strokeStyle = '#050506'; ctx.lineWidth = 3;
  for (let i = -1; i < 7; i++) {
    const x0 = i * 520 - (px % 520), sag = 50 + (i & 1) * 30;
    ctx.beginPath(); ctx.moveTo(x0, -10); ctx.quadraticCurveTo(x0 + 260, 70 + sag, x0 + 520, -10); ctx.stroke();
    if (i & 1) for (let b = 1; b < 8; b++) {
      const t = b / 8, x = (1 - t) * (1 - t) * x0 + 2 * (1 - t) * t * (x0 + 260) + t * t * (x0 + 520), y = (1 - t) * (1 - t) * -10 + 2 * (1 - t) * t * (70 + sag) + t * t * -10;
      const g = ctx.createRadialGradient(x, y + 6, 0, x, y + 6, 22); g.addColorStop(0, 'rgba(255,200,120,0.9)'); g.addColorStop(0.15, 'rgba(255,170,80,0.5)'); g.addColorStop(1, 'rgba(255,150,60,0)');
      ctx.fillStyle = g; ctx.fillRect(x - 22, y - 16, 44, 44);
    }
  }
  ctx.restore();
}

// ------------------------------------------------ fighters
function drawFighter(e, mode) {
  const sx = e.x - G.camX, sy = e.y;
  const rec = frameRec(e);
  if (!rec) return;
  const flip = (e.face === 1 ? 1 : -1) * rec.flip;
  // idle breathing / landing squash
  const breathe = (e.state === 'idle' ? Math.sin(G.t * 3 + (e.seed || 0)) * 0.008 : 0) - (e.squash || 0);
  const sX = rec.s * (1 - breathe * 0.5), sY = rec.s * (1 + breathe);
  if (mode === 'silhouette') {
    const oc = litSprite(e, rec, 'silhouette');
    ctx.save(); ctx.translate(sx, sy - e.z); ctx.scale(flip * sX, sY); ctx.drawImage(oc, -rec.fr.ax, -rec.fr.ay); ctx.restore(); return;
  }
  // contact shadow
  const sh = ctx.createRadialGradient(sx, sy + 4, 4, sx, sy + 4, e.kind === 'boss' ? 95 : 75);
  sh.addColorStop(0, 'rgba(0,0,0,0.55)'); sh.addColorStop(1, 'rgba(0,0,0,0)');
  ctx.fillStyle = sh; ctx.beginPath(); ctx.ellipse(sx, sy + 4, e.kind === 'boss' ? 95 : 75, 18, 0, 0, Math.PI * 2); ctx.fill();

  let alpha = 1;
  if (e.state === 'dead' && e.t > 1.5) alpha = Math.max(0, 1 - (e.t - 1.5));
  if (e.kind === 'kurt' && e.inv > 0 && e.state !== 'down' && Math.floor(G.t * 20) % 2) alpha *= 0.6;
  const tele = e.state === 'windup' && e.atk && e.t > e.atk.wind - 0.25;
  const oc = litSprite(e, rec, tele ? 'tele' : null);

  // wet-street reflection
  ctx.save(); ctx.globalAlpha = 0.17 * alpha;
  ctx.translate(sx, sy + 2 + e.z * 0.6); ctx.scale(flip * sX, -sY * 0.62);
  ctx.drawImage(oc, -rec.fr.ax, -rec.fr.ay); ctx.restore();

  // afterimages on fast moves
  if (e.trail) for (const t of e.trail) {
    ctx.save(); ctx.globalAlpha = t.a * alpha; ctx.translate(t.x - G.camX, t.y - t.z); ctx.scale(t.flip * t.s, t.s);
    ctx.globalCompositeOperation = 'lighter'; ctx.filter = e.flameT > 0 ? 'hue-rotate(180deg) saturate(2)' : 'sepia(1) saturate(3) hue-rotate(-20deg)';
    ctx.drawImage(t.img, -t.ax, -t.ay); ctx.restore();
  }
  ctx.save(); ctx.globalAlpha = alpha;
  ctx.translate(sx, sy - e.z); ctx.scale(flip * sX, sY);
  if (e.kind === 'kurt' && e.flameT > 0) { ctx.shadowColor = '#3d9cff'; ctx.shadowBlur = 24 + Math.sin(G.t * 20) * 8; }
  ctx.drawImage(oc, -rec.fr.ax, -rec.fr.ay);
  ctx.restore();
  // Blue Flame: licking flame particles off the body
  if (e.kind === 'kurt' && e.flameT > 0 && Math.random() < 0.45) G.fx.push({ flame: true, x: e.x + rnd(-40, 40), y: e.y - rnd(30, 260), vx: rnd(-20, 20), vy: rnd(-260, -120), life: rnd(0.25, 0.5), max: 0.5, r: rnd(6, 14) });
  if (e.kind === 'kurt' && e.state === 'grabbed') bar(sx - 60, sy - e.h - 50, 120, 10, e.mash / 8, '#ffd27a');
}
function recordTrails(dt) {
  for (const e of [G.kurt, ...G.foes]) {
    if (!e) continue;
    const fast = (e.kind === 'kurt' && (e.state === 'attack' || e.state === 'dodge')) || e.state === 'airborne';
    e.trail = (e.trail || []).map(t => ({ ...t, a: t.a - dt * 3.2 })).filter(t => t.a > 0);
    if (fast && (e.trailT = (e.trailT || 0) - dt) <= 0) {
      e.trailT = 0.035;
      const rec = frameRec(e); if (!rec) continue;
      const c = document.createElement('canvas'); c.width = rec.fr.w; c.height = rec.fr.h; c.getContext('2d').drawImage(rec.img, 0, 0);
      e.trail.push({ img: c, x: e.x, y: e.y, z: e.z, s: rec.s, flip: (e.face === 1 ? 1 : -1) * rec.flip, ax: rec.fr.ax, ay: rec.fr.ay, a: 0.2 });
      if (e.trail.length > 4) e.trail.shift();
    }
    if (e.hitFlash > 0) e.hitFlash -= dt;
    if (e.squash > 0) e.squash = Math.max(0, e.squash - dt * 0.6);
  }
}

// ------------------------------------------------ fx
function drawFx() {
  for (const p of G.fx) {
    const a = clamp(p.life / p.max, 0, 1), x = p.x - G.camX;
    ctx.save();
    if (p.flame) {
      ctx.globalCompositeOperation = 'lighter';
      const g = ctx.createRadialGradient(x, p.y, 0, x, p.y, p.r * 2);
      g.addColorStop(0, `rgba(170,220,255,${a})`); g.addColorStop(0.4, `rgba(60,150,255,${a * 0.7})`); g.addColorStop(1, 'rgba(30,90,255,0)');
      ctx.fillStyle = g; ctx.fillRect(x - p.r * 2, p.y - p.r * 2, p.r * 4, p.r * 4);
    } else if (p.star) {
      // ink-style impact star: jagged white burst with a coloured rim
      const sc = (1 - a) * 0.6 + 0.7, R = p.r * sc;
      ctx.translate(x, p.y); ctx.rotate(p.rot);
      ctx.beginPath();
      for (let i = 0; i < p.n * 2; i++) { const ang = i / (p.n * 2) * Math.PI * 2, rr = i % 2 ? R * 0.32 : R * (0.75 + p.j[i % p.j.length] * 0.5); ctx.lineTo(Math.cos(ang) * rr, Math.sin(ang) * rr); }
      ctx.closePath(); ctx.globalAlpha = a;
      ctx.fillStyle = '#fffaf0'; ctx.fill(); ctx.lineWidth = 4; ctx.strokeStyle = p.c; ctx.stroke();
    } else if (p.ring) {
      ctx.globalAlpha = a; ctx.strokeStyle = p.c; ctx.lineWidth = 5 * a;
      ctx.beginPath(); ctx.arc(x, p.y, p.r * (1.6 - a), 0, 7); ctx.stroke();
    } else if (p.dust) {
      ctx.globalAlpha = a * 0.5; ctx.fillStyle = '#8a8580';
      ctx.beginPath(); ctx.arc(x, p.y, p.r * (1.6 - a), 0, 7); ctx.fill();
    } else {
      ctx.globalAlpha = a; ctx.strokeStyle = p.c; ctx.lineWidth = p.r * 0.7; ctx.lineCap = 'round';
      ctx.beginPath(); ctx.moveTo(x, p.y); ctx.lineTo(x - p.vx * 0.03, p.y - p.vy * 0.03); ctx.stroke();
    }
    ctx.restore();
  }
}
function speedLines(cx, cy, n, inner, color, alpha) {
  ctx.save(); ctx.strokeStyle = color; ctx.globalAlpha = alpha;
  for (let i = 0; i < n; i++) {
    const a = (i / n) * Math.PI * 2 + rnd(-0.05, 0.05), r0 = inner + rnd(0, 60), r1 = r0 + rnd(200, 600);
    ctx.lineWidth = rnd(1.5, 5);
    ctx.beginPath(); ctx.moveTo(cx + Math.cos(a) * r0, cy + Math.sin(a) * r0); ctx.lineTo(cx + Math.cos(a) * r1, cy + Math.sin(a) * r1); ctx.stroke();
  }
  ctx.restore();
}

// ------------------------------------------------ HUD
const HUD = { kurtLag: 1, foeLag: 1 };
function slantBar(x, y, w, h, v, lag, c1, c2) {
  ctx.save(); ctx.transform(1, 0, -0.35, 1, 0, 0);
  ctx.fillStyle = 'rgba(0,0,0,0.55)'; ctx.fillRect(x + y * 0.35 - 3, y - 3, w + 6, h + 6);
  ctx.fillStyle = 'rgba(255,240,220,0.55)'; ctx.fillRect(x + y * 0.35, y, w * clamp(lag, 0, 1), h);
  const g = ctx.createLinearGradient(0, y, 0, y + h); g.addColorStop(0, c1); g.addColorStop(1, c2);
  ctx.fillStyle = g; ctx.fillRect(x + y * 0.35, y, w * clamp(v, 0, 1), h);
  ctx.fillStyle = 'rgba(255,255,255,0.25)'; ctx.fillRect(x + y * 0.35, y, w * clamp(v, 0, 1), h * 0.3);
  ctx.restore();
}
function portrait(img, x, y, r, ring) {
  ctx.save(); ctx.beginPath(); ctx.arc(x, y, r, 0, 7); ctx.closePath();
  ctx.fillStyle = '#111'; ctx.fill(); ctx.clip();
  if (img.complete && img.naturalWidth) ctx.drawImage(img, x - r, y - r, r * 2, r * 2);
  ctx.restore();
  ctx.strokeStyle = ring; ctx.lineWidth = 4; ctx.beginPath(); ctx.arc(x, y, r, 0, 7); ctx.stroke();
}
function drawHUD(dt) {
  const k = G.kurt;
  HUD.kurtLag += (k.hp / k.maxhp - HUD.kurtLag) * Math.min(1, dt * 2.5);
  portrait(PORTRAIT.kurt, 78, 74, 44, k.flameT > 0 ? '#4aa8ff' : '#d9a441');
  ctx.font = '700 22px Oswald, "Segoe UI", sans-serif'; ctx.textAlign = 'left'; ctx.fillStyle = '#f1e6d4';
  ctx.fillText('KURT SLOANE', 134, 52);
  slantBar(134, 60, 360, 16, k.hp / k.maxhp, HUD.kurtLag, k.hp < 35 ? '#ff6a50' : '#f6d27a', k.hp < 35 ? '#a8221a' : '#c48a2c');
  const fv = k.flameT > 0 ? k.flameT / 9 : k.flame / 100;
  slantBar(140, 84, 240, 8, fv, fv, '#9fd6ff', '#2c78d8');
  ctx.font = '600 11px Oswald, "Segoe UI", sans-serif'; ctx.fillStyle = k.flame >= 100 || k.flameT > 0 ? '#9cd4ff' : '#8ea4c0';
  ctx.fillText(k.flameT > 0 ? 'BLUE FLAME' : k.flame >= 100 ? 'FLAME READY — F' : 'FLAME', 390, 92);

  const boss = G.foes.find(e => e.kind === 'boss' && e.state !== 'dead');
  const show = boss || (G.focus && G.focus.f);
  if (show && show.state !== 'dead') {
    HUD.foeLag += (show.hp / show.maxhp - HUD.foeLag) * Math.min(1, dt * 2.5);
    const bw = boss ? 460 : 280;
    if (boss) portrait(PORTRAIT.boss, W - 78, 74, 44, '#a8322a');
    ctx.textAlign = 'right'; ctx.font = '700 22px Oswald, "Segoe UI", sans-serif'; ctx.fillStyle = '#f1e6d4';
    ctx.fillText(show.name, W - (boss ? 134 : 40), 52);
    slantBar(W - (boss ? 134 : 40) - bw - 20, 60, bw, boss ? 16 : 12, show.hp / show.maxhp, HUD.foeLag, '#ff5a46', '#7a1410');
  } else HUD.foeLag = 1;
  if (G.combo >= 3) {
    ctx.save(); ctx.textAlign = 'left'; ctx.translate(40, 190); ctx.rotate(-0.06);
    const pop = 1 + Math.max(0, G.comboT - 1.3) * 2;
    ctx.scale(pop, pop);
    ctx.font = 'italic 700 58px Oswald, "Segoe UI", sans-serif'; ctx.lineWidth = 6; ctx.strokeStyle = '#1a0e08';
    ctx.strokeText(G.combo, 0, 0); ctx.fillStyle = '#ffd27a'; ctx.fillText(G.combo, 0, 0);
    const w = ctx.measureText(G.combo).width;
    ctx.font = 'italic 700 24px Oswald, "Segoe UI", sans-serif'; ctx.strokeText('HITS', w + 8, 0); ctx.fillStyle = '#f1e6d4'; ctx.fillText('HITS', w + 8, 0);
    ctx.restore();
  }
  for (const t of G.texts) {
    ctx.globalAlpha = clamp(t.life / 0.8, 0, 1); ctx.textAlign = 'center';
    ctx.font = '700 22px Oswald, "Segoe UI", sans-serif'; ctx.lineWidth = 4; ctx.strokeStyle = '#000';
    ctx.strokeText(t.s, t.x - G.camX, t.y); ctx.fillStyle = t.c; ctx.fillText(t.s, t.x - G.camX, t.y); ctx.globalAlpha = 1;
  }
  if (!G.lock && G.wave < WAVES.length && G.mode === 'play' && Math.floor(G.t * 2) % 2) {
    ctx.textAlign = 'right'; ctx.font = '700 44px Oswald, "Segoe UI", sans-serif'; ctx.fillStyle = '#ffd27a'; ctx.fillText('GO ›', W - 40, H / 2);
  }
  ctx.textAlign = 'left'; ctx.font = '500 12px "Segoe UI", sans-serif'; ctx.fillStyle = 'rgba(241,230,212,0.55)';
  ctx.fillText('MOVE arrows/WASD  ·  J punch combo  ·  K roundhouse  ·  L fire knee  ·  U spinning backfist  ·  E clinch  ·  SPACE block  ·  SHIFT dodge  ·  F blue flame  ·  M mute', 40, H - 16);
}
function bar(x, y, w, h, v, c, back = 'rgba(0,0,0,0.6)') {
  ctx.fillStyle = back; ctx.fillRect(x - 2, y - 2, w + 4, h + 4);
  ctx.fillStyle = c; ctx.fillRect(x, y, w * clamp(v, 0, 1), h);
}
function drawBanner() {
  const b = G.banner; if (!b) return;
  const a = clamp(Math.min(b.t, 0.4) / 0.4, 0, 1);
  ctx.save(); ctx.globalAlpha = a; ctx.textAlign = 'center';
  const g = ctx.createLinearGradient(0, 0, W, 0); g.addColorStop(0, 'rgba(0,0,0,0)'); g.addColorStop(0.5, 'rgba(0,0,0,0.7)'); g.addColorStop(1, 'rgba(0,0,0,0)');
  ctx.fillStyle = g; ctx.fillRect(0, H * 0.36, W, b.sub ? 124 : 88);
  ctx.font = '700 64px Oswald, "Segoe UI", sans-serif'; ctx.fillStyle = b.blue ? '#8cc9ff' : '#f1e6d4';
  if (b.blue) { ctx.shadowColor = '#3d9cff'; ctx.shadowBlur = 30; }
  ctx.fillText(b.text, W / 2, H * 0.36 + 66);
  ctx.shadowBlur = 0;
  if (b.sub) { ctx.font = 'italic 400 20px "Segoe UI", sans-serif'; ctx.fillStyle = '#d9a441'; ctx.fillText(b.sub, W / 2, H * 0.36 + 102); }
  ctx.restore();
}
function drawCard(title, sub, foot) {
  ctx.fillStyle = 'rgba(8,5,4,0.7)'; ctx.fillRect(0, 0, W, H);
  ctx.textAlign = 'center';
  ctx.font = '700 92px Oswald, "Segoe UI", sans-serif'; ctx.fillStyle = '#f1e6d4'; ctx.fillText(title, W / 2, H * 0.42);
  ctx.font = 'italic 400 26px "Segoe UI", sans-serif'; ctx.fillStyle = '#d9a441'; ctx.fillText(sub, W / 2, H * 0.42 + 54);
  if (foot && Math.floor(performance.now() / 500) % 2) { ctx.font = '600 20px Oswald, "Segoe UI", sans-serif'; ctx.fillStyle = '#f1e6d4'; ctx.fillText(foot, W / 2, H * 0.72); }
}

// ------------------------------------------------ frame
let lastR = performance.now();
function render() {
  const now = performance.now(), rdt = Math.min(0.05, (now - lastR) / 1000); lastR = now;
  ctx.setTransform(cv.width / W, 0, 0, cv.height / H, 0, 0);
  if (G.kurt) recordTrails(G.hitstop > 0 ? 0 : rdt);
  G.zoom = 1 + ((G.zoom || 1) - 1) * Math.pow(0.02, rdt);
  ctx.save();
  // camera punch-in around Kurt, then shake
  if (G.kurt && G.zoom > 1.001) { const fx = G.kurt.x - G.camX, fy = G.kurt.y - 180; ctx.translate(fx, fy); ctx.scale(G.zoom, G.zoom); ctx.translate(-fx, -fy); }
  if (G.shake > 0) ctx.translate(rnd(-1, 1) * G.shake * 30, rnd(-1, 1) * G.shake * 22);
  drawBG(); drawSteam();
  const all = G.kurt ? [G.kurt, ...G.foes].sort((a, b) => a.y - b.y) : [];
  for (const e of all) drawFighter(e);
  drawFx();
  drawRain(rdt);
  drawForeground();
  // anime impact frame: paper-white world, ink-black fighters, speed lines
  if (G.impact > 0) {
    G.impact -= rdt;
    ctx.fillStyle = 'rgba(246,240,228,0.94)'; ctx.fillRect(-50, -50, W + 100, H + 100);
    for (const e of all) drawFighter(e, 'silhouette');
    if (G.impactAt) speedLines(G.impactAt.x - G.camX, G.impactAt.y, 46, 60, '#0b0a0a', 0.9);
  }
  ctx.restore();

  // grade: teal shadows / warm highlights, vignette, grain
  ctx.save(); ctx.globalCompositeOperation = 'soft-light';
  const gr = ctx.createLinearGradient(0, 0, 0, H); gr.addColorStop(0, 'rgba(30,80,120,0.55)'); gr.addColorStop(1, 'rgba(210,120,60,0.45)');
  ctx.fillStyle = gr; ctx.fillRect(0, 0, W, H); ctx.restore();
  const v = ctx.createRadialGradient(W / 2, H * 0.55, H * 0.3, W / 2, H * 0.55, H * 0.95);
  v.addColorStop(0, 'rgba(0,0,0,0)'); v.addColorStop(1, G.kurt && G.kurt.flameT > 0 ? 'rgba(8,30,80,0.65)' : 'rgba(0,0,0,0.6)');
  ctx.fillStyle = v; ctx.fillRect(0, 0, W, H);
  if (!grainPat) grainPat = ctx.createPattern(NOISE, 'repeat');
  ctx.save(); ctx.globalAlpha = 0.07; ctx.globalCompositeOperation = 'overlay';
  ctx.translate(Math.random() * 256, Math.random() * 256); ctx.fillStyle = grainPat; ctx.fillRect(-256, -256, W + 512, H + 512); ctx.restore();
  if (G.flash > 0) { ctx.fillStyle = `rgba(255,240,220,${G.flash})`; ctx.fillRect(0, 0, W, H); }
  // cinematic bars for the boss entrance
  if (G.cine > 0) { G.cine -= rdt; const b = 70 * clamp(Math.min(G.cine, 0.4) / 0.4, 0, 1); ctx.fillStyle = '#000'; ctx.fillRect(0, 0, W, b); ctx.fillRect(0, H - b, W, b); }

  if (G.mode === 'title') {
    drawCard('KICKBOXER: REBIRTH', 'Chapter 1 — The Door', 'PRESS ENTER / J  (or A on a controller)');
    ctx.font = '500 16px "Segoe UI", sans-serif'; ctx.fillStyle = 'rgba(241,230,212,0.75)';
    ctx.fillText('J punch combo (jab · cross · elbow · fire knee)   K roundhouse   L fire knee   U spinning backfist', W / 2, H * 0.82);
    ctx.fillText('E clinch + L knees   SPACE block   SHIFT dodge   F Blue Flame when the meter is full', W / 2, H * 0.82 + 24);
    return;
  }
  drawHUD(rdt); drawBanner();
  if (G.mode === 'pause') drawCard('PAUSED', 'Press P to resume');
  if (G.mode === 'over') drawCard('YOU WERE BROKEN', 'Get up. The door is still there.', G.endT > 1.2 ? 'PRESS ENTER TO TRY AGAIN' : '');
  if (G.mode === 'win') drawCard('THE DOOR OPENS', 'Tournament entry granted. The ladder begins.', G.endT > 1.2 ? 'PRESS ENTER TO PLAY AGAIN' : '');
}

// ---------------------------------------------------------------- loop
let last = performance.now();
function loop(now) {
  const dt = Math.min(0.05, (now - last) / 1000); last = now;
  const consumed = update(dt) !== false;
  render();
  if (consumed) for (const k in pressed) delete pressed[k];
  requestAnimationFrame(loop);
}
requestAnimationFrame(loop);
// for automated checks (the browser pauses rAF in hidden tabs, so tests can step the game by hand)
window.__G = G;
window.__step = (dt = 1 / 60) => { const c = update(dt) !== false; render(); if (c) for (const k in pressed) delete pressed[k]; };
