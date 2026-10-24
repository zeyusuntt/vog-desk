// The night over Kaʻū: terrain, the summit vent, the monitors, tonight's GFS wind and the inversion from the Hilo
// sounding, with each measured hour as a column at its monitor. Everything drawn is data from scene.json except
// the drift particles, which only move the 925 hPa wind forward from the vent and are labelled as an illustration.
import * as THREE from "three";

const EXAG = 2.0;                  // vertical scale, shown on the panel
const SECONDS_PER_HOUR = 1.25;     // playback speed
const NIGHT = { bg: 0x0f171b, ocean: 0x0d161a };
const LEVEL = (ppb) => ppb >= 1010 ? "#cc4a7c" : ppb >= 210 ? "#c4561c" : ppb >= 110 ? "#d6a90e" : "#3b8c55";

async function loadTerrain(base) {
  const meta = await fetch(`${base}assets/kau-terrain.json`).then((r) => r.json());
  const blob = await fetch(`${base}assets/kau-terrain.png`).then((r) => r.blob());
  const bmp = await createImageBitmap(blob, { colorSpaceConversion: "none", premultiplyAlpha: "none" });
  const cv = document.createElement("canvas"); cv.width = bmp.width; cv.height = bmp.height;
  const ctx = cv.getContext("2d", { willReadFrequently: true }); ctx.drawImage(bmp, 0, 0);
  const px = ctx.getImageData(0, 0, bmp.width, bmp.height).data;
  const h = new Float32Array(bmp.width * bmp.height);
  for (let i = 0; i < h.length; i++) h[i] = ((px[i * 4] * 256 + px[i * 4 + 1]) / 65535) * meta.elevMaxM;
  return { meta, w: bmp.width, hgt: bmp.height, h };
}

export async function mountNight(root, scene, opts = {}) {
  const base = opts.base || "./";
  const canvas = root.querySelector("canvas");
  let renderer;
  try { renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: "low-power" }); }
  catch { root.classList.add("no-webgl"); return null; }
  const T = await loadTerrain(base);
  const { box, kmWidth: KW, kmHeight: KH } = T.meta;
  const toX = (lon) => (lon - box.lonMin) / (box.lonMax - box.lonMin) * KW - KW / 2;
  const toZ = (lat) => (box.latMax - lat) / (box.latMax - box.latMin) * KH - KH / 2;
  const heightAt = (x, z) => {               // km above sea level, exaggerated, bilinear on the grid
    const fx = Math.min(T.w - 1.001, Math.max(0, (x + KW / 2) / KW * (T.w - 1)));
    const fz = Math.min(T.hgt - 1.001, Math.max(0, (z + KH / 2) / KH * (T.hgt - 1)));
    const x0 = Math.floor(fx), z0 = Math.floor(fz), tx = fx - x0, tz = fz - z0, i = z0 * T.w + x0;
    const v = T.h[i] * (1 - tx) * (1 - tz) + T.h[i + 1] * tx * (1 - tz) + T.h[i + T.w] * (1 - tx) * tz + T.h[i + T.w + 1] * tx * tz;
    return v / 1000 * EXAG;
  };

  const s3 = new THREE.Scene();
  s3.background = new THREE.Color(NIGHT.bg);
  s3.fog = new THREE.Fog(NIGHT.bg, 120, 220);
  const camera = new THREE.PerspectiveCamera(30, 2, 1, 600);
  const target = new THREE.Vector3(-2, 0, -4);

  // Terrain: a cartographic night relief. Hillshade from a low northwest light, cool elevation tint, a contour every
  // 250 m (heavier every 1,000 m) and a faint rim along the coast. Elevation in metres drives tint and contours.
  const segW = Math.round(T.w / 2), segH = Math.round(T.hgt / 2);
  const geo = new THREE.PlaneGeometry(KW, KH, segW, segH); geo.rotateX(-Math.PI / 2);
  const pos = geo.attributes.position, elev = new Float32Array(pos.count);
  for (let i = 0; i < pos.count; i++) { const y = heightAt(pos.getX(i), pos.getZ(i)); pos.setY(i, y); elev[i] = y / EXAG * 1000; }
  geo.setAttribute("elev", new THREE.BufferAttribute(elev, 1)); geo.computeVertexNormals();
  const terrainMat = new THREE.ShaderMaterial({
    uniforms: { uLight: { value: new THREE.Vector3(-0.62, 0.48, -0.62).normalize() }, uFog: { value: new THREE.Color(NIGHT.bg) } },
    vertexShader: `attribute float elev; varying float vE; varying vec3 vN; varying float vD;
      void main(){ vE=elev; vN=normal; vec4 mv=modelViewMatrix*vec4(position,1.0); vD=-mv.z; gl_Position=projectionMatrix*mv; }`,
    fragmentShader: `uniform vec3 uLight; uniform vec3 uFog; varying float vE; varying vec3 vN; varying float vD;
      vec3 ramp(float e){
        vec3 c0=vec3(0.118,0.165,0.188), c1=vec3(0.165,0.220,0.247), c2=vec3(0.235,0.298,0.329), c3=vec3(0.345,0.412,0.447), c4=vec3(0.520,0.580,0.612);
        if(e<300.0) return mix(c0,c1,e/300.0);
        if(e<1200.0) return mix(c1,c2,(e-300.0)/900.0);
        if(e<2600.0) return mix(c2,c3,(e-1200.0)/1400.0);
        return mix(c3,c4,clamp((e-2600.0)/1550.0,0.0,1.0)); }
      void main(){
        vec3 sea=vec3(0.051,0.086,0.102);
        if(vE<1.5){ gl_FragColor=vec4(mix(sea,uFog,clamp((vD-110.0)/120.0,0.0,1.0)),1.0); return; }
        vec3 n=normalize(vN);
        float lit=max(dot(n,uLight),0.0);
        float shade=0.42+0.95*lit-0.25*(1.0-n.y);
        vec3 col=ramp(vE)*shade;
        float f=vE/250.0, w=fwidth(f);
        float line=1.0-smoothstep(0.0,w*1.2,abs(fract(f-0.5)-0.5));
        float major=1.0-smoothstep(0.0,w*1.6,abs(fract(vE/1000.0-0.5)-0.5)*4.0);
        col+=vec3(0.62,0.74,0.80)*(0.055*line+0.07*major);
        col+=vec3(0.40,0.62,0.72)*0.16*(1.0-smoothstep(1.5,40.0,vE));
        col=mix(col,uFog,clamp((vD-110.0)/120.0,0.0,1.0));
        gl_FragColor=vec4(col,1.0); }`,
  });
  s3.add(new THREE.Mesh(geo, terrainMat));
  const sea = new THREE.Mesh(new THREE.PlaneGeometry(600, 600), new THREE.MeshBasicMaterial({ color: NIGHT.ocean, fog: true }));
  sea.rotateX(-Math.PI / 2); sea.position.y = -0.02; s3.add(sea);

  // The inversion from the Hilo sounding: a faint lid the gas stays under.
  if (scene.inversion) {
    const yl = scene.inversion.base_m / 1000 * EXAG;
    // Faded toward its edges so it reads as a layer of air, not a box.
    const lid = new THREE.Mesh(new THREE.PlaneGeometry(KW, KH), new THREE.ShaderMaterial({ transparent: true, depthWrite: false, side: THREE.DoubleSide,
      vertexShader: "varying vec2 vUv; void main(){ vUv=uv; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0); }",
      fragmentShader: "varying vec2 vUv; void main(){ float e=min(min(vUv.x,1.0-vUv.x),min(vUv.y,1.0-vUv.y)); float a=0.11*smoothstep(0.0,0.22,e); gl_FragColor=vec4(0.62,0.80,0.88,a); }" }));
    lid.rotateX(-Math.PI / 2); lid.position.y = yl; s3.add(lid);
  }

  // The vent and the monitors.
  const vx = toX(scene.vent.lon), vz = toZ(scene.vent.lat), vy = heightAt(vx, vz);
  const glowTex = (() => { const g = document.createElement("canvas"); g.width = g.height = 64; const x = g.getContext("2d");
    const r = x.createRadialGradient(32, 32, 0, 32, 32, 32); r.addColorStop(0, "rgba(255,170,90,1)"); r.addColorStop(0.35, "rgba(255,120,50,.45)"); r.addColorStop(1, "rgba(255,90,30,0)");
    x.fillStyle = r; x.fillRect(0, 0, 64, 64); return new THREE.CanvasTexture(g); })();
  const active = /fountaining/.test(scene.episode || "");
  const vent = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTex, transparent: true, depthWrite: false, opacity: active ? 1 : 0.55 }));
  vent.position.set(vx, vy + 0.3, vz); vent.scale.setScalar(active ? 6 : 3.5); s3.add(vent);

  const stations = scene.stations.map((st) => {
    const x = toX(st.lon), z = toZ(st.lat), y = heightAt(x, z);
    const pin = new THREE.Mesh(new THREE.CylinderGeometry(0.12, 0.12, 1, 10), new THREE.MeshBasicMaterial({ color: 0xe7ecee }));
    pin.position.set(x, y + 0.5, z); s3.add(pin);
    const ring = new THREE.Mesh(new THREE.RingGeometry(0.7, 0.95, 32), new THREE.MeshBasicMaterial({ color: 0xe7ecee, transparent: true, opacity: 0.85, side: THREE.DoubleSide }));
    ring.rotateX(-Math.PI / 2); ring.position.set(x, y + 0.05, z); s3.add(ring);
    const colMat = new THREE.MeshBasicMaterial({ color: 0x3b8c55, transparent: true, opacity: 0.92 });
    const column = new THREE.Mesh(new THREE.CylinderGeometry(0.55, 0.55, 1, 20), colMat);
    column.visible = false; s3.add(column);
    return { ...st, x, y, z, column, colMat };
  });

  // Drift from the vent at the 925 hPa wind (illustration). Particles hug the terrain and stay under the lid.
  const N = 1100, pp = new Float32Array(N * 3), age = new Float32Array(N), life = 7, alpha = new Float32Array(N);
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const lidY = scene.inversion ? scene.inversion.base_m / 1000 * EXAG : Infinity;
  const spawn = (i) => { pp[i * 3] = vx + (Math.random() - 0.5) * 2.4; pp[i * 3 + 2] = vz + (Math.random() - 0.5) * 2.4; age[i] = Math.random() * life; };
  for (let i = 0; i < N; i++) spawn(i);
  const pgeo = new THREE.BufferGeometry();
  pgeo.setAttribute("position", new THREE.BufferAttribute(pp, 3)); pgeo.setAttribute("alpha", new THREE.BufferAttribute(alpha, 1));
  const pmat = new THREE.ShaderMaterial({ transparent: true, depthWrite: false, uniforms: { size: { value: 3.0 * Math.min(devicePixelRatio, 2) } },
    vertexShader: "attribute float alpha; varying float va; uniform float size; void main(){ va=alpha; vec4 mv=modelViewMatrix*vec4(position,1.0); gl_PointSize=size*(60.0/-mv.z); gl_Position=projectionMatrix*mv; }",
    fragmentShader: "varying float va; void main(){ vec2 d=gl_PointCoord-0.5; float r=length(d); if(r>0.5) discard; gl_FragColor=vec4(0.93,0.90,0.80,va*(1.0-r*1.6)); }" });
  const drift = new THREE.Points(pgeo, pmat); s3.add(drift);

  // Wind at an hour: GFS leads at 18:00, 00:00 and 06:00, interpolated between them and held after 06:00.
  const leads = [0, 6, 12];
  const windAt = (k) => {
    const w = scene.wind; if (!w.length) return null;
    const vec = (o) => { const to = (o.from_deg + 180) * Math.PI / 180; return [Math.sin(to) * o.speed_ms, -Math.cos(to) * o.speed_ms]; };
    if (k <= leads[0] || w.length === 1) return { v: vec(w[0]), lead: w[0] };
    for (let j = 0; j < w.length - 1; j++) if (k <= leads[j + 1]) {
      const t = (k - leads[j]) / (leads[j + 1] - leads[j]), a = vec(w[j]), b = vec(w[j + 1]);
      return { v: [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t], lead: t < 0.5 ? w[j] : w[j + 1] };
    }
    return { v: vec(w[w.length - 1]), lead: w[w.length - 1] };
  };

  // Labels: HTML over the canvas, so text stays crisp and readable by screen readers.
  const overlay = root.querySelector(".scene-labels");
  const mk = (cls, html) => { const d = document.createElement("div"); d.className = `scene-label ${cls}`; d.innerHTML = html; overlay.append(d); return d; };
  const ventLabel = mk("vent left", `<b>${scene.vent.name}</b><span>${active ? "fountaining" : "summit vent"}</span>`);
  stations.forEach((st) => { st.label = mk(`station${st.id === "hvnp" ? " right" : ""}`, `<b>${st.name}</b><span class="v"></span>`); });

  // State and drawing.
  let hour = opts.hour ?? 0, playing = false, last = performance.now(), visible = true, raf = 0;
  const hours = scene.hours;
  const setHour = (k) => {
    hour = Math.max(0, Math.min(17, k));
    stations.forEach((st) => {
      const v = hours ? hours[st.id]?.[Math.floor(hour)] : null;
      const span = st.label.querySelector(".v");
      if (v == null) { st.column.visible = false; span.textContent = hours ? "no value" : "not measured yet"; st.label._w = st.label._h = undefined; return; }
      const hk = 1.6 * Math.sqrt(Math.max(v, 0) / 75) + 0.15;   // square root of SO2, so 75 ppb stands 1.75 km and 1,200 ppb 6.6 km
      st.column.visible = true; st.column.scale.set(1, hk, 1); st.column.position.set(st.x, st.y + hk / 2, st.z);
      st.colMat.color.set(LEVEL(v)); span.textContent = `${Math.round(v)} ppb${v >= 75 ? ", above 75" : ""}`;
      st.label.classList.toggle("hot", v >= 75); st.label._w = st.label._h = undefined;
    });
    opts.onHour?.(Math.floor(hour), windAt(hour));
  };

  const resize = () => {
    const w = root.clientWidth, narrow = w < 560, h = narrow ? Math.round(w * 0.92) : Math.round(Math.min(560, Math.max(320, w * 0.56)));
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2)); renderer.setSize(w, h, false);
    canvas.style.width = `${w}px`; canvas.style.height = `${h}px`; camera.aspect = w / h;
    const far = narrow ? 1.3 : 1;
    camera.position.set(12 * far, 54 * far, 76 * far); camera.lookAt(target); camera.updateProjectionMatrix();
  };
  // Labels are placed in script: anchored above a point, or beside it for the vent and the summit monitor; kept inside
  // the canvas; and nudged upward when two would overlap, the forecast's monitor first.
  const project = (x, y, z, el) => {
    const v = new THREE.Vector3(x, y, z).project(camera);
    el._px = (v.x + 1) / 2 * canvas.clientWidth; el._py = (1 - v.y) / 2 * canvas.clientHeight;
  };
  const layout = (els) => {
    const W = canvas.clientWidth, H = canvas.clientHeight, ox = canvas.offsetLeft, oy = canvas.offsetTop, placed = [];
    for (const el of els) {
      const w = el._w ?? (el._w = el.offsetWidth), h = el._h ?? (el._h = el.offsetHeight);
      let left = el.classList.contains("left") ? el._px - w - 10 : el.classList.contains("right") ? el._px + 10 : el._px - w / 2;
      let top = el.classList.contains("left") || el.classList.contains("right") ? el._py - h / 2 : el._py - h - 4;
      if (el.classList.contains("left") && left < 6) left = el._px + 10;
      if (el.classList.contains("right") && left + w > W - 6) left = el._px - w - 10;
      left = Math.max(6, Math.min(W - w - 6, left));
      for (let pass = 0; pass < 4; pass++) for (const r of placed)
        if (left < r.left + r.w + 4 && left + w + 4 > r.left && top < r.top + r.h + 4 && top + h + 4 > r.top) top = r.top - h - 4;
      top = Math.max(4, Math.min(H - h - 4, top));
      placed.push({ left, top, w, h });
      el.style.transform = `translate(${(left + ox).toFixed(1)}px, ${(top + oy).toFixed(1)}px)`;
    }
  };

  let px = 0, py = 0;
  root.addEventListener("pointermove", (e) => { if (e.pointerType !== "mouse") return; const r = root.getBoundingClientRect(); px = (e.clientX - r.left) / r.width - 0.5; py = (e.clientY - r.top) / r.height - 0.5; });

  const step = (now) => {
    const dt = Math.min(0.1, (now - last) / 1000); last = now;
    if (playing) { hour += dt / SECONDS_PER_HOUR; if (hour >= 17.999) { hour = 17; playing = false; opts.onPlay?.(false); } setHour(hour); }
    const w = windAt(hour);
    if (w && !reduce) {
      const kmPerSec = 3.6 / SECONDS_PER_HOUR;                 // one second on screen is 1/SECONDS_PER_HOUR hours of real drift
      for (let i = 0; i < N; i++) {
        age[i] += dt;
        if (age[i] > life) spawn(i), age[i] = 0;
        pp[i * 3] += (w.v[0] + (Math.random() - 0.5) * 1.4) * kmPerSec * dt;
        pp[i * 3 + 2] += (w.v[1] + (Math.random() - 0.5) * 1.4) * kmPerSec * dt;
        const g = heightAt(pp[i * 3], pp[i * 3 + 2]);
        pp[i * 3 + 1] = Math.min(Math.max(g, 0) + 0.35, lidY - 0.1);
        alpha[i] = Math.min(1, age[i] * 2) * (1 - age[i] / life) * 0.8;
      }
      pgeo.attributes.position.needsUpdate = true; pgeo.attributes.alpha.needsUpdate = true;
    }
    camera.position.x += ((12 + px * 10) - camera.position.x) * 0.04;
    camera.lookAt(target.x, target.y - py * 3, target.z);
    renderer.render(s3, camera);
    project(vx - 0.8, vy + 0.6, vz, ventLabel);
    stations.forEach((st) => project(st.x, st.y + (st.column.visible ? st.column.scale.y + 0.4 : 1.4), st.z, st.label));
    layout([...stations.map((st) => st.label), ventLabel]);
    if (visible && (!reduce || playing)) raf = requestAnimationFrame(step); else raf = 0;
  };
  const kick = () => { if (!raf) { last = performance.now(); raf = requestAnimationFrame(step); } };

  if (reduce && scene.wind.length) {                          // static drift lines instead of moving particles
    for (let i = 0; i < N; i++) {
      const t = (i % 100) / 100 * 8, lane = Math.floor(i / 100) - 5, w = windAt(Math.min(17, t));
      const side = [-w.v[1], w.v[0]], n = Math.hypot(...side) || 1;
      pp[i * 3] = vx + w.v[0] * 3.6 * t + side[0] / n * lane * 0.35; pp[i * 3 + 2] = vz + w.v[1] * 3.6 * t + side[1] / n * lane * 0.35;
      pp[i * 3 + 1] = Math.min(Math.max(heightAt(pp[i * 3], pp[i * 3 + 2]), 0) + 0.35, lidY - 0.1); alpha[i] = 0.5 * (1 - t / 8);
    }
    pgeo.attributes.position.needsUpdate = true; pgeo.attributes.alpha.needsUpdate = true;
  }

  new IntersectionObserver(([e]) => { visible = e.isIntersecting; if (visible) kick(); }).observe(root);
  addEventListener("resize", () => { resize(); [ventLabel, ...stations.map((st) => st.label)].forEach((el) => { el._w = el._h = undefined; }); kick(); });
  resize(); setHour(hour); kick();
  return {
    setHour: (k) => { playing = false; setHour(k); kick(); },
    play: () => { if (hour >= 17) hour = 0; playing = true; kick(); opts.onPlay?.(true); },
    pause: () => { playing = false; opts.onPlay?.(false); },
    get playing() { return playing; },
  };
}
