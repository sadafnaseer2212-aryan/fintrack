/* FinTrack 3D visualisations (Three.js, ES module, no build step).
 *  - bars3d : interactive 3D income / expenses / savings overview
 *  - ring3d : depth-scaled 3D expense-category ring
 *  - terrain: ambient animated "market terrain" for the sign-in page
 * Performance: device-pixel-ratio capped, rendering pauses when off-screen or the tab is hidden,
 * reduced-motion users get static scenes, and callers provide a 2D fallback when WebGL is missing. */
import * as THREE from '../vendor/three.module.min.js';

const REDUCED = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const easeOut = (t) => 1 - Math.pow(1 - Math.min(Math.max(t, 0), 1), 3);
const fmtPKR = (v) => (v < 0 ? '-' : '') + 'Rs ' + Math.round(Math.abs(v)).toLocaleString('en-US');

/* ------------------------------------------------------------------ base stage */
class Stage {
  constructor(el, {fov = 36, dpr = 1.75, interactive = true} = {}) {
    this.el = el;
    this.renderer = new THREE.WebGLRenderer({antialias: true, alpha: true, powerPreference: 'low-power'});
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, dpr));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.1;
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(fov, 1, 0.1, 200);
    this.target = new THREE.Vector3();
    this.theta = -0.6; this.phi = 1.05; this.radius = 14; this.baseTheta = this.theta;
    this.dragging = false; this.userMoved = 0; this.clock = new THREE.Clock(); this.visible = true; this.running = true;
    const c = this.renderer.domElement;
    c.setAttribute('tabindex', '0');
    c.style.touchAction = 'pan-y';
    el.prepend(c);
    this.resize();
    this.ro = new ResizeObserver(() => this.resize()); this.ro.observe(el);
    this.io = new IntersectionObserver(([e]) => { this.visible = e.isIntersecting; }); this.io.observe(el);
    document.addEventListener('visibilitychange', () => { this.running = !document.hidden; });
    if (interactive) this.bindControls(c);
    this.frame = this.frame.bind(this);
    requestAnimationFrame(this.frame);
  }
  resize() {
    const w = this.el.clientWidth || 300, h = this.el.clientHeight || 200;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h; this.camera.updateProjectionMatrix();
    this.w = w; this.h = h;
  }
  bindControls(c) {
    let lx = 0, ly = 0;
    c.addEventListener('pointerdown', (e) => { this.dragging = true; lx = e.clientX; ly = e.clientY; c.setPointerCapture(e.pointerId); });
    c.addEventListener('pointermove', (e) => {
      if (this.dragging) {
        const dx = e.clientX - lx, dy = e.clientY - ly; lx = e.clientX; ly = e.clientY;
        this.theta -= dx * 0.008; this.phi = Math.min(1.42, Math.max(0.45, this.phi - dy * 0.006));
        this.userMoved = performance.now();
      }
      this.onHover?.(e);
    });
    const up = (e) => { this.dragging = false; try { c.releasePointerCapture(e.pointerId); } catch (_) {} };
    c.addEventListener('pointerup', up); c.addEventListener('pointercancel', up);
    c.addEventListener('pointerleave', () => this.onLeave?.());
    c.addEventListener('dblclick', () => { this.theta = this.baseTheta; this.phi = this.basePhi ?? 1.05; this.userMoved = 0; });
    c.addEventListener('keydown', (e) => {
      const k = {ArrowLeft: [0.12, 0], ArrowRight: [-0.12, 0], ArrowUp: [0, -0.08], ArrowDown: [0, 0.08]}[e.key];
      if (k) { e.preventDefault(); this.theta += k[0]; this.phi = Math.min(1.42, Math.max(0.45, this.phi + k[1])); this.userMoved = performance.now(); }
    });
  }
  updateCamera(t) {
    // gentle idle sway (stops while/after the user interacts, and for reduced motion)
    if (!REDUCED && !this.dragging && performance.now() - this.userMoved > 4000 && this.sway !== false) {
      this.theta += ((this.baseTheta + Math.sin(t * 0.18) * 0.22) - this.theta) * 0.02;
    }
    const p = this.phi, th = this.theta, r = this.radius;
    this.camera.position.set(this.target.x + r * Math.sin(p) * Math.sin(th), this.target.y + r * Math.cos(p), this.target.z + r * Math.sin(p) * Math.cos(th));
    this.camera.lookAt(this.target);
  }
  project(v3) {
    const v = v3.clone().project(this.camera);
    return {x: (v.x * 0.5 + 0.5) * this.w, y: (-v.y * 0.5 + 0.5) * this.h, behind: v.z > 1};
  }
  frame() {
    requestAnimationFrame(this.frame);
    if (!this.visible || !this.running) return;
    const t = this.clock.getElapsedTime();
    this.tick?.(t);
    this.updateCamera(t);
    this.renderer.render(this.scene, this.camera);
    this.afterRender?.(t);
  }
  lights() {
    this.scene.add(new THREE.HemisphereLight(0xcffff0, 0x08110e, 0.75));
    const d = new THREE.DirectionalLight(0xffffff, 1.35); d.position.set(6, 12, 8); this.scene.add(d);
    const p = new THREE.PointLight(0x34d399, 30, 30, 2); p.position.set(-6, 5, 6); this.scene.add(p);
  }
}

function tooltip(el) {
  const t = document.createElement('div'); t.className = 'tip3d'; el.appendChild(t); return t;
}
function placeTip(tip, el, x, y) {
  const w = tip.offsetWidth || 160, h = tip.offsetHeight || 60;
  tip.style.left = Math.min(Math.max(8, x + 14), el.clientWidth - w - 8) + 'px';
  tip.style.top = Math.min(Math.max(8, y - h - 10), el.clientHeight - h - 8) + 'px';
}

/* ------------------------------------------------------------------ 3D bars */
export function bars3d(el, {labels, series}) {
  const st = new Stage(el, {fov: 34});
  st.lights();
  const N = labels.length, S = series.length;
  const maxAbs = Math.max(1, ...series.flatMap(s => s.values.map(Math.abs)));
  const MAXH = 4.2, dx = 1.05, dz = 1.25, bw = 0.58;
  const group = new THREE.Group(); st.scene.add(group);
  const geo = new THREE.BoxGeometry(bw, 1, bw * 0.92); geo.translate(0, 0.5, 0);
  const bars = [];
  series.forEach((s, si) => {
    const base = new THREE.Color(s.color);
    s.values.forEach((v, i) => {
      const neg = v < 0;
      const col = neg ? new THREE.Color('#f87171') : base;
      const mat = new THREE.MeshStandardMaterial({color: col, roughness: 0.32, metalness: 0.18, emissive: col, emissiveIntensity: 0.1, transparent: true, opacity: 1});
      const m = new THREE.Mesh(geo, mat);
      const h = Math.max(0.02, Math.abs(v) / maxAbs * MAXH);
      m.position.set((i - (N - 1) / 2) * dx, 0, (si - (S - 1) / 2) * dz);
      m.userData = {i, si, v, h, neg, delay: i * 0.045 + si * 0.12};
      m.scale.y = REDUCED ? h : 0.001;
      if (neg && REDUCED) m.position.y = -h;
      group.add(m); bars.push(m);
    });
  });
  // floor: dark glossy plane + fine grid
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(N * dx + 3, S * dz + 3), new THREE.MeshStandardMaterial({color: 0x0b1411, roughness: 0.6, metalness: 0.4, transparent: true, opacity: 0.85}));
  floor.rotation.x = -Math.PI / 2; floor.position.y = -0.01; group.add(floor);
  const grid = new THREE.GridHelper(Math.max(N * dx, S * dz) + 3, Math.round(N + 3), 0x1f3a31, 0x14241f);
  grid.material.transparent = true; grid.material.opacity = 0.5; group.add(grid);

  st.target.set(0, 1.2, 0.3); st.radius = Math.max(15, N * 1.55); st.theta = st.baseTheta = -0.42; st.phi = st.basePhi = 1.1;
  // fit the whole chart in view for any aspect ratio (desktop panel or narrow phone)
  const fit = () => {
    const vfov = st.camera.fov * Math.PI / 180, hfov = 2 * Math.atan(Math.tan(vfov / 2) * st.camera.aspect);
    const halfW = (N * dx) / 2 + 0.8, halfH = MAXH / 2 + 1.2;
    st.radius = Math.max(halfW / Math.tan(hfov / 2), halfH / Math.tan(vfov / 2)) * 1.3;
  };
  fit(); new ResizeObserver(fit).observe(el);
  // month labels along the front row
  const front = (S - 1) / 2 * dz + 0.9;
  const step = N > 8 ? 2 : 1;   // label every other month when crowded (always label the latest)
  const labelEls = labels.map((l, i) => {
    const d = document.createElement('div'); d.className = 's-label'; d.textContent = l; el.appendChild(d);
    if (i % step !== (N - 1) % step) d.style.display = 'none';
    return {d, pos: new THREE.Vector3((i - (N - 1) / 2) * dx, 0, front)};
  });
  const tip = tooltip(el);
  const ray = new THREE.Raycaster(), mouse = new THREE.Vector2();
  let hovered = null;
  st.onHover = (e) => {
    const r = el.getBoundingClientRect();
    mouse.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(mouse, st.camera);
    const hit = ray.intersectObjects(bars)[0]?.object || null;
    if (hit !== hovered) {
      hovered = hit;
      bars.forEach(b => { const same = hit && b.userData.i === hit.userData.i; b.material.emissiveIntensity = b === hit ? 0.6 : same ? 0.28 : 0.1; b.material.opacity = !hit || same ? 1 : 0.45; });
    }
    if (hit) {
      const i = hit.userData.i;
      tip.innerHTML = `<div class="text-muted" style="font-size:.7rem">${labels[i]}</div>` + series.map((s, si) =>
        `<div class="d-flex justify-content-between gap-3"><span><span class="legend-dot" style="background:${s.values[i] < 0 ? '#f87171' : s.color}"></span> ${s.name}</span><b>${fmtPKR(s.values[i])}</b></div>`).join('');
      tip.classList.add('show'); placeTip(tip, el, e.clientX - r.left, e.clientY - r.top);
    } else tip.classList.remove('show');
  };
  st.onLeave = () => { hovered = null; tip.classList.remove('show'); bars.forEach(b => { b.material.emissiveIntensity = 0.1; b.material.opacity = 1; }); };
  st.tick = (t) => {
    if (REDUCED) return;
    bars.forEach(b => {
      const {h, delay, neg} = b.userData;
      const k = easeOut((t - 0.15 - delay) / 1.1);
      b.scale.y = Math.max(0.001, h * k);
      b.position.y = neg ? -h * k : 0;
      const s = b === hovered ? 1.1 : 1; b.scale.x += (s - b.scale.x) * 0.2; b.scale.z = b.scale.x;
    });
  };
  st.afterRender = () => labelEls.forEach(({d, pos}) => {
    const p = st.project(pos); d.style.left = p.x + 'px'; d.style.top = (p.y + 4) + 'px'; d.style.opacity = p.behind ? 0 : 1;
  });
  return st;
}

/* ------------------------------------------------------------------ 3D category ring */
export function ring3d(el, {labels, values, colors, onFocus}) {
  const st = new Stage(el, {fov: 32});
  st.lights();
  const total = values.reduce((a, b) => a + b, 0) || 1;
  const R = 2.25, r = 1.35, gap = 0.035;
  const group = new THREE.Group(); st.scene.add(group);
  const segs = []; let a0 = Math.PI / 2;
  values.forEach((v, i) => {
    const share = v / total, a1 = a0 - share * Math.PI * 2;
    const s = new THREE.Shape();
    const from = a0 - gap / 2, to = Math.min(a1 + gap / 2, from - 0.01);
    s.absarc(0, 0, R, from, to, true);
    s.absarc(0, 0, r, to, from, false);
    s.closePath();
    const depth = 0.3 + share * 1.6;
    const g = new THREE.ExtrudeGeometry(s, {depth, bevelEnabled: true, bevelThickness: 0.04, bevelSize: 0.03, bevelSegments: 2, curveSegments: 40});
    g.rotateX(-Math.PI / 2);
    const col = new THREE.Color(colors[i % colors.length]);
    const m = new THREE.Mesh(g, new THREE.MeshStandardMaterial({color: col, roughness: 0.3, metalness: 0.2, emissive: col, emissiveIntensity: 0.08}));
    const mid = (a0 + a1) / 2;
    m.userData = {i, share, v, dir: new THREE.Vector3(Math.cos(mid), 0, -Math.sin(mid)), lift: 0};
    m.scale.y = REDUCED ? 1 : 0.001;
    group.add(m); segs.push(m); a0 = a1;
  });
  const base = new THREE.Mesh(new THREE.CircleGeometry(R + 0.6, 64), new THREE.MeshStandardMaterial({color: 0x0b1411, roughness: 0.7, metalness: 0.3, transparent: true, opacity: 0.7}));
  base.rotation.x = -Math.PI / 2; base.position.y = -0.02; group.add(base);
  st.target.set(0, 0.2, 0); st.radius = 9.2; st.theta = st.baseTheta = 0; st.phi = st.basePhi = 0.78; st.sway = false;
  const tip = tooltip(el), ray = new THREE.Raycaster(), mouse = new THREE.Vector2();
  let hovered = null;
  st.onHover = (e) => {
    const rc = el.getBoundingClientRect();
    mouse.set(((e.clientX - rc.left) / rc.width) * 2 - 1, -((e.clientY - rc.top) / rc.height) * 2 + 1);
    ray.setFromCamera(mouse, st.camera);
    const hit = ray.intersectObjects(segs)[0]?.object || null;
    if (hit !== hovered) { hovered = hit; onFocus?.(hit ? hit.userData.i : null); }
    if (hit) {
      const {i, share, v} = hit.userData;
      tip.innerHTML = `<div class="d-flex align-items-center gap-2"><span class="legend-dot" style="background:${colors[i % colors.length]}"></span><b>${labels[i]}</b></div><div>${fmtPKR(v)} · ${(share * 100).toFixed(1)}%</div>`;
      tip.classList.add('show'); placeTip(tip, el, e.clientX - rc.left, e.clientY - rc.top);
    } else tip.classList.remove('show');
  };
  st.onLeave = () => { hovered = null; tip.classList.remove('show'); onFocus?.(null); };
  st.tick = (t) => {
    if (!REDUCED && !st.dragging && performance.now() - st.userMoved > 4000 && !hovered) group.rotation.y += 0.0025;
    segs.forEach((m, k) => {
      if (!REDUCED) m.scale.y = Math.max(0.001, easeOut((t - 0.1 - k * 0.08) / 0.9));
      const target = m === hovered ? 0.32 : 0;
      m.userData.lift += (target - m.userData.lift) * 0.18;
      m.position.copy(m.userData.dir).multiplyScalar(m.userData.lift * 0.6); m.position.y = m.userData.lift;
      m.material.emissiveIntensity = m === hovered ? 0.45 : 0.08;
    });
  };
  return st;
}

/* ------------------------------------------------------------------ sign-in terrain */
export function terrain(el) {
  const st = new Stage(el, {fov: 42, dpr: 1.25, interactive: false});
  st.scene.fog = new THREE.Fog(0x060a09, 14, 34);
  st.lights();
  const COLS = 34, ROWS = 18, sp = 0.62;
  const geo = new THREE.BoxGeometry(0.4, 1, 0.4); geo.translate(0, 0.5, 0);
  const mat = new THREE.MeshStandardMaterial({roughness: 0.35, metalness: 0.25, emissive: 0x0b3b2c, emissiveIntensity: 0.35});
  const mesh = new THREE.InstancedMesh(geo, mat, COLS * ROWS);
  const dummy = new THREE.Object3D(), c = new THREE.Color(), low = new THREE.Color('#1a2622'), high = new THREE.Color('#34d399');
  st.scene.add(mesh);
  // floating forecast line
  const pts = []; for (let i = 0; i <= 60; i++) { const x = (i / 60 - 0.5) * COLS * sp; pts.push(new THREE.Vector3(x, 3.2 + Math.sin(i / 7) * 0.5 + i * 0.03, -2)); }
  const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({color: 0x6ee7b7, transparent: true, opacity: 0.8}));
  st.scene.add(line);
  st.target.set(0, 0.8, 0); st.radius = 15; st.theta = st.baseTheta = -0.35; st.phi = 1.18; st.sway = !REDUCED;
  const layout = (t) => {
    let n = 0;
    for (let x = 0; x < COLS; x++) for (let z = 0; z < ROWS; z++) {
      const h = 0.25 + 1.6 * (0.5 + 0.5 * Math.sin(x * 0.38 + t * 0.9) * Math.cos(z * 0.45 - t * 0.6)) + 0.9 * Math.max(0, Math.sin((x + z) * 0.18 + t * 0.4));
      dummy.position.set((x - COLS / 2) * sp, 0, (z - ROWS / 2) * sp); dummy.scale.set(1, h, 1); dummy.updateMatrix();
      mesh.setMatrixAt(n, dummy.matrix);
      mesh.setColorAt(n, c.copy(low).lerp(high, Math.min(1, (h - 0.25) / 2.8)));
      n++;
    }
    mesh.instanceMatrix.needsUpdate = true; if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  };
  layout(1.2);
  st.tick = (t) => { if (!REDUCED) { layout(t); line.position.y = Math.sin(t * 0.5) * 0.15; } };
  return st;
}

export const webglAvailable = () => {
  try { const c = document.createElement('canvas'); return !!(c.getContext('webgl2') || c.getContext('webgl')); } catch (e) { return false; }
};
