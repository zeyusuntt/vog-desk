// The night over Kaʻū: terrain, the summit vent, the monitors, tonight's GFS wind and the inversion from the Hilo
// sounding, with each measured hour as a beam at its monitor. Everything drawn is data from scene.json except
// the drift particles, which only move the 925 hPa wind forward from the vent and are labelled as an illustration.
import * as THREE from "three";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const EXAG = 2.0;                  // vertical scale, shown on the panel
const SECONDS_PER_HOUR = 1.25;     // playback speed
// Hourly SO2 on the same warm ramp as the strip, in a luminous version for the night map.
const BEAM = [[0, "#8fb3c2"], [25, "#d2c9a2"], [50, "#e8bd78"], [75, "#efa058"], [110, "#ec8448"], [210, "#e2623f"], [1010, "#d8455c"]];
const LEVEL = (v) => { let i = BEAM.findIndex(([a]) => v < a); if (i === -1) return BEAM[BEAM.length - 1][1]; if (i === 0) return BEAM[0][1];
  const [a0, c0] = BEAM[i - 1], [a1, c1] = BEAM[i], t = (v - a0) / (a1 - a0), h = (c) => [1, 3, 5].map((k) => parseInt(c.slice(k, k + 2), 16)), x = h(c0), y = h(c1);
  return "#" + x.map((q, k) => Math.round(q + (y[k] - q) * t).toString(16).padStart(2, "0")).join(""); };

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
  const sky = (() => { const c = document.createElement("canvas"); c.width = 2; c.height = 256; const g = c.getContext("2d");
    const gr = g.createLinearGradient(0, 0, 0, 256); gr.addColorStop(0, "#070d10"); gr.addColorStop(0.55, "#0e1a20"); gr.addColorStop(1, "#1a2b33");
    g.fillStyle = gr; g.fillRect(0, 0, 2, 256); const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; return t; })();
  s3.background = new THREE.Color(0x070d10);
  const skyMat = new THREE.ShaderMaterial({ side: THREE.BackSide, depthWrite: false, fog: false,
    uniforms: { uTop: { value: new THREE.Color(0x070d10) }, uBottom: { value: new THREE.Color(0x1a2b33) } },
    vertexShader: "varying vec3 vP; void main(){ vP=normalize(position); gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0); }",
    fragmentShader: "uniform vec3 uTop; uniform vec3 uBottom; varying vec3 vP; void main(){ float t=smoothstep(-0.05,0.55,vP.y); gl_FragColor=vec4(mix(uBottom,uTop,t),1.0); }" });
  const skyDome = new THREE.Mesh(new THREE.SphereGeometry(420, 32, 16), skyMat); s3.add(skyDome);
  sky.dispose();
  const FOG = new THREE.Color(0x16252c);
  const camera = new THREE.PerspectiveCamera(30, 2, 1, 600);
  const target = new THREE.Vector3(-2, 0, -4);
  renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 1.2;

  // Terrain: a night relief. A low northwest key light and a soft northeast fill, an elevation tint, faint contours every
  // 250 m, a surf line on the coast and, around the vent, a warm glow that is stronger while an episode is fountaining.
  const active = /fountaining/.test(scene.episode || "");
  const vx = toX(scene.vent.lon), vz = toZ(scene.vent.lat), vy = heightAt(vx, vz);
  const segW = Math.round(T.w / 2), segH = Math.round(T.hgt / 2);
  const geo = new THREE.PlaneGeometry(KW, KH, segW, segH); geo.rotateX(-Math.PI / 2);
  const pos = geo.attributes.position, elev = new Float32Array(pos.count);
  for (let i = 0; i < pos.count; i++) { const y = heightAt(pos.getX(i), pos.getZ(i)); pos.setY(i, y); elev[i] = y / EXAG * 1000; }
  geo.setAttribute("elev", new THREE.BufferAttribute(elev, 1)); geo.computeVertexNormals();
  const terrainMat = new THREE.ShaderMaterial({
    uniforms: { uKey: { value: new THREE.Vector3(-0.66, 0.42, -0.62).normalize() }, uFill: { value: new THREE.Vector3(0.7, 0.55, -0.45).normalize() },
      uFog: { value: FOG }, uVent: { value: new THREE.Vector2(vx, vz) }, uGlow: { value: active ? 1.0 : 0.35 }, uDay: { value: 0.0 }, uFogN: { value: 95 }, uFogF: { value: 210 } },
    vertexShader: `attribute float elev; varying float vE; varying vec3 vN; varying vec2 vXZ; varying float vD;
      void main(){ vE=elev; vN=normal; vXZ=position.xz; vec4 mv=modelViewMatrix*vec4(position,1.0); vD=-mv.z; gl_Position=projectionMatrix*mv; }`,
    fragmentShader: `uniform vec3 uKey; uniform vec3 uFill; uniform vec3 uFog; uniform vec2 uVent; uniform float uGlow; uniform float uDay; uniform float uFogN; uniform float uFogF;
      varying float vE; varying vec3 vN; varying vec2 vXZ; varying float vD;
      vec3 lin(vec3 c){ return pow(c, vec3(2.2)); }
      vec3 ramp(float e){
        vec3 c0=lin(vec3(0.16,0.20,0.21)), c1=lin(vec3(0.21,0.26,0.27)), c2=lin(vec3(0.29,0.33,0.34)), c3=lin(vec3(0.40,0.43,0.44)), c4=lin(vec3(0.62,0.64,0.65));
        if(e<300.0) return mix(c0,c1,e/300.0);
        if(e<1200.0) return mix(c1,c2,(e-300.0)/900.0);
        if(e<2600.0) return mix(c2,c3,(e-1200.0)/1400.0);
        return mix(c3,c4,clamp((e-2600.0)/1550.0,0.0,1.0)); }
      void main(){
        if(vE<1.5) discard;
        vec3 n=normalize(vN);
        float key=max(dot(n,uKey),0.0), fill=max(dot(n,uFill),0.0), skyl=0.5+0.5*n.y, slope=1.0-n.y;
        vec3 col=ramp(vE)*(0.30*skyl+(1.1+0.35*uDay)*key+0.34*fill)*(1.0-0.30*slope)*(1.0+0.25*uDay);
        float f=vE/250.0, w=fwidth(f);
        float line=1.0-smoothstep(0.0,w*1.1,abs(fract(f-0.5)-0.5));
        col+=lin(vec3(0.55,0.68,0.74))*0.035*line;
        col+=lin(vec3(0.45,0.70,0.80))*0.22*(1.0-smoothstep(1.5,18.0,vE));
        float d=distance(vXZ,uVent);
        col+=lin(vec3(1.0,0.42,0.12))*uGlow*0.55*(1.0-0.7*uDay)*exp(-d*d/8.0);
        col=mix(col,uFog,smoothstep(uFogN,uFogF,vD)*0.9);
        gl_FragColor=vec4(col,1.0); }`,
  });
  s3.add(new THREE.Mesh(geo, terrainMat));
  const sea = new THREE.Mesh(new THREE.PlaneGeometry(600, 600), new THREE.ShaderMaterial({ uniforms: { uFog: { value: FOG }, uFogN: { value: 80 }, uFogF: { value: 230 } },
    vertexShader: "varying float vD; void main(){ vec4 mv=modelViewMatrix*vec4(position,1.0); vD=-mv.z; gl_Position=projectionMatrix*mv; }",
    fragmentShader: "uniform vec3 uFog; uniform float uFogN; uniform float uFogF; varying float vD; void main(){ vec3 c=pow(vec3(0.035,0.065,0.08),vec3(2.2)); gl_FragColor=vec4(mix(c,uFog,smoothstep(uFogN,uFogF,vD)*0.85),1.0); }" }));
  sea.rotateX(-Math.PI / 2); sea.position.y = -0.02; s3.add(sea);

  // The inversion from the Hilo sounding: a faint lid the gas stays under, faded toward its edges.
  if (scene.inversion) {
    const yl = scene.inversion.base_m / 1000 * EXAG;
    const lid = new THREE.Mesh(new THREE.PlaneGeometry(KW, KH), new THREE.ShaderMaterial({ transparent: true, depthWrite: false, side: THREE.DoubleSide,
      vertexShader: "varying vec2 vUv; void main(){ vUv=uv; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0); }",
      fragmentShader: "varying vec2 vUv; void main(){ float e=min(min(vUv.x,1.0-vUv.x),min(vUv.y,1.0-vUv.y)); gl_FragColor=vec4(0.30,0.55,0.66,0.06*smoothstep(0.0,0.25,e)); }" }));
    lid.rotateX(-Math.PI / 2); lid.position.y = yl; s3.add(lid);
  }

  // Soft round textures for glows, halos and the plume.
  const radial = (stops) => { const g = document.createElement("canvas"); g.width = g.height = 128; const x = g.getContext("2d");
    const r = x.createRadialGradient(64, 64, 0, 64, 64, 64); stops.forEach(([o, c]) => r.addColorStop(o, c)); x.fillStyle = r; x.fillRect(0, 0, 128, 128);
    const t = new THREE.CanvasTexture(g); t.colorSpace = THREE.SRGBColorSpace; return t; };
  const glowTex = radial([[0, "rgba(255,255,255,1)"], [0.25, "rgba(255,255,255,.55)"], [1, "rgba(255,255,255,0)"]]);
  const haloTex = radial([[0, "rgba(255,255,255,0)"], [0.62, "rgba(255,255,255,0)"], [0.78, "rgba(255,255,255,.9)"], [0.86, "rgba(255,255,255,.25)"], [1, "rgba(255,255,255,0)"]]);
  const additive = (map, color, opacity) => new THREE.SpriteMaterial({ map, color, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, opacity });

  // The vent: a warm glow, larger while fountaining.
  const vent = new THREE.Sprite(additive(glowTex, new THREE.Color(1.0, 0.5, 0.18), active ? 1.0 : 0.6));
  vent.position.set(vx, vy + 0.35, vz); vent.scale.setScalar(active ? 7 : 4); s3.add(vent);
  const ventCore = new THREE.Sprite(additive(glowTex, new THREE.Color(1.0, 0.85, 0.6), 0.9));
  ventCore.position.copy(vent.position); ventCore.scale.setScalar(active ? 1.8 : 1.1); s3.add(ventCore);

  // Where people live: a few warm lights (Pāhala, Nāʻālehu, Ocean View, Volcano village).
  for (const [lat, lon] of [[19.2039, -155.4802], [19.0647, -155.5856], [19.1176, -155.7781], [19.4422, -155.2353]]) {
    const x = toX(lon), z = toZ(lat); const l = new THREE.Sprite(additive(glowTex, new THREE.Color(1.0, 0.78, 0.48), 0.55));
    l.position.set(x, heightAt(x, z) + 0.12, z); l.scale.setScalar(1.6); s3.add(l);
  }

  // The monitors: a ground halo and a beam that fades upward, topped by a bright point. Beam height grows with the
  // square root of SO2 and the colour follows the schools plan's bands; with no value only a small white mark shows.
  const beamGeo = new THREE.CylinderGeometry(0.24, 0.24, 1, 16, 1, true); beamGeo.translate(0, 0.5, 0);
  const beamMat = (color) => new THREE.ShaderMaterial({ transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, side: THREE.DoubleSide,
    uniforms: { uColor: { value: color } },
    vertexShader: "varying float vY; void main(){ vY=position.y; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0); }",
    fragmentShader: "uniform vec3 uColor; varying float vY; void main(){ gl_FragColor=vec4(uColor*1.6,(0.95-0.75*vY)); }" });
  const stations = scene.stations.map((st) => {
    const x = toX(st.lon), z = toZ(st.lat), y = heightAt(x, z);
    const color = new THREE.Color(0xffffff);
    const mark = new THREE.Sprite(additive(glowTex, new THREE.Color(0.9, 0.95, 1.0), 0.9)); mark.position.set(x, y + 0.15, z); mark.scale.setScalar(0.9); s3.add(mark);
    const halo = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), new THREE.MeshBasicMaterial({ map: haloTex, color, transparent: true, depthWrite: false, depthTest: false, blending: THREE.AdditiveBlending, opacity: 0.85 }));
    halo.rotateX(-Math.PI / 2); halo.position.set(x, y + 0.2, z); halo.renderOrder = 2; s3.add(halo);
    const mat = beamMat(color);
    const beam = new THREE.Mesh(beamGeo, mat); beam.position.set(x, y, z); s3.add(beam);
    const head = new THREE.Sprite(additive(glowTex, color, 1.0)); s3.add(head);
    const core = new THREE.Mesh(new THREE.CircleGeometry(0.32, 20), new THREE.MeshBasicMaterial({ color: 0xf2f5f6, depthTest: false }));
    core.rotateX(-Math.PI / 2); core.position.set(x, y + 0.25, z); core.renderOrder = 3; s3.add(core);
    const t75 = 2.8 + 0.2;   // beam height at 75 ppb
    const ring = new THREE.Mesh(new THREE.TorusGeometry(0.42, 0.045, 8, 32), new THREE.MeshBasicMaterial({ color: 0xf2f5f6, transparent: true, opacity: 0.8 }));
    ring.rotateX(-Math.PI / 2); ring.position.set(x, y + t75, z); s3.add(ring);
    return { ...st, x, y, z, color, mat, beam, head, halo, mark, ring, hot: false, beamH: 0 };
  });

  // Drift from the vent at the 925 hPa wind (illustration): soft puffs that spread and fade downwind, hug the terrain
  // and stay under the inversion. Normal blending, so where many overlap in a weak wind they thicken to a pale haze
  // instead of adding up to white.
  const N = 1600, pp = new Float32Array(N * 3), age = new Float32Array(N), life = 8, alpha = new Float32Array(N), psize = new Float32Array(N);
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const lidY = scene.inversion ? scene.inversion.base_m / 1000 * EXAG : Infinity;
  const spawn = (i) => { pp[i * 3] = vx + (Math.random() - 0.5) * 1.6; pp[i * 3 + 2] = vz + (Math.random() - 0.5) * 1.6; age[i] = Math.random() * life; };
  for (let i = 0; i < N; i++) spawn(i);
  const pgeo = new THREE.BufferGeometry();
  pgeo.setAttribute("position", new THREE.BufferAttribute(pp, 3)); pgeo.setAttribute("alpha", new THREE.BufferAttribute(alpha, 1)); pgeo.setAttribute("psize", new THREE.BufferAttribute(psize, 1));
  const pmat = new THREE.ShaderMaterial({ transparent: true, depthWrite: false, blending: THREE.NormalBlending, uniforms: { size: { value: 5.0 * Math.min(devicePixelRatio, 2) }, fade: { value: 1 } },
    vertexShader: "attribute float alpha; attribute float psize; varying float va; uniform float size; void main(){ va=alpha; vec4 mv=modelViewMatrix*vec4(position,1.0); gl_PointSize=size*psize*(60.0/-mv.z); gl_Position=projectionMatrix*mv; }",
    fragmentShader: "uniform float fade; varying float va; void main(){ vec2 d=gl_PointCoord-0.5; float r=length(d); if(r>0.5) discard; float s=exp(-r*r*14.0); gl_FragColor=vec4(vec3(0.46,0.45,0.41),va*s*fade*0.6); }" });
  const drift = new THREE.Points(pgeo, pmat); s3.add(drift);

  // Wind lines across the whole map: short streaks that move with the 925 hPa wind at the summit point.
  const NW = 420, wpos = new Float32Array(NW * 6), wage = new Float32Array(NW), wlife = 5, walpha = new Float32Array(NW * 2);
  const wspawn = (i) => { const x = (Math.random() - 0.5) * KW, z = (Math.random() - 0.5) * KH; wpos.set([x, 0, z, x, 0, z], i * 6); wage[i] = Math.random() * wlife; };
  for (let i = 0; i < NW; i++) wspawn(i);
  const wgeo = new THREE.BufferGeometry();
  wgeo.setAttribute("position", new THREE.BufferAttribute(wpos, 3)); wgeo.setAttribute("alpha", new THREE.BufferAttribute(walpha, 1));
  const wmat = new THREE.ShaderMaterial({ transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, uniforms: { fade: { value: 1 } },
    vertexShader: "attribute float alpha; varying float va; void main(){ va=alpha; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0); }",
    fragmentShader: "uniform float fade; varying float va; void main(){ gl_FragColor=vec4(vec3(0.62,0.80,0.90)*0.55, va*fade); }" });
  const windLines = new THREE.LineSegments(wgeo, wmat); s3.add(windLines);

  // Bloom on the bright parts only (vent, beams, plume, town lights); the terrain stays below the threshold.
  const composer = new EffectComposer(renderer);
  composer.addPass(new RenderPass(s3, camera));
  const bloom = new UnrealBloomPass(new THREE.Vector2(512, 512), 0.6, 0.5, 0.5);
  composer.addPass(bloom); composer.addPass(new OutputPass());

  // Camera: drag to turn, ⌘ or Ctrl with the wheel (or a trackpad pinch) to zoom, so the page still scrolls; on touch
  // screens the map takes gestures only after "Explore the map". Viewpoints fly the camera; the first view flies in once.
  const controls = new OrbitControls(camera, canvas);
  Object.assign(controls, { enableDamping: true, dampingFactor: 0.08, enableZoom: false, rotateSpeed: 0.55, panSpeed: 0.7,
    screenSpacePanning: false, minDistance: 26, maxDistance: 200, minPolarAngle: 0.0, maxPolarAngle: 1.22,
    minAzimuthAngle: -0.75, maxAzimuthAngle: 1.05 });  // the map is always seen from the sea, never from behind the mountains
  controls.target.copy(target);
  const coarse = matchMedia("(pointer: coarse)").matches;
  controls.enabled = !coarse;
  const home = { pos: new THREE.Vector3(12, 54, 76), target: target.clone() };
  let userMoved = false, fly = null, introPending = !reduce, selected = null;
  const ease = (t) => 1 - Math.pow(1 - Math.min(1, Math.max(0, t)), 3);
  const flyTo = (pos, tgt, dur = 1200) => { fly = { p0: camera.position.clone(), p1: pos.clone(), t0: controls.target.clone(), t1: tgt.clone(), start: performance.now(), dur: reduce ? 1 : dur }; kick(); };
  controls.addEventListener("start", () => { userMoved = true; fly = null; });
  const hint = root.querySelector(".scene-hint");
  const say = (msg) => { if (!hint) return; hint.textContent = msg; hint.classList.add("on"); clearTimeout(say.t); say.t = setTimeout(() => hint.classList.remove("on"), 1600); };
  canvas.addEventListener("wheel", (e) => {
    if (!(e.ctrlKey || e.metaKey)) { say(navigator.platform.includes("Mac") ? "Hold ⌘ and scroll to zoom the map" : "Hold Ctrl and scroll to zoom the map"); return; }
    e.preventDefault();
    const off = camera.position.clone().sub(controls.target), d = THREE.MathUtils.clamp(off.length() * Math.exp(e.deltaY * 0.002), controls.minDistance, controls.maxDistance);
    camera.position.copy(controls.target).add(off.setLength(d)); userMoved = true; fly = null; kick();
  }, { passive: false });
  const views = {
    overview: () => [home.pos, home.target],
    summit: () => { const t = new THREE.Vector3((vx + toX(-155.4802)) / 2, 0.6, (vz + toZ(19.2039)) / 2); return [t.clone().add(new THREE.Vector3(22, 26, 40)), t]; },
    top: () => [new THREE.Vector3(0.5, 150, 0.6), new THREE.Vector3(0, 0, 0)],
    oceanview: () => { const t = new THREE.Vector3(toX(-155.7781), 0.8, toZ(19.1176)); return [t.clone().add(new THREE.Vector3(16, 22, 32)), t]; },
  };

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

  // Click a monitor (or its label, or the vent) to fly to it and open its night: 18 hours on the same ramp as the strip.
  const pickMat = new THREE.MeshBasicMaterial({ visible: false }), picks = [];
  stations.forEach((st) => { const m = new THREE.Mesh(new THREE.SphereGeometry(2.6, 12, 8), pickMat); m.position.set(st.x, st.y + 1.2, st.z); m.userData.st = st; s3.add(m); picks.push(m); });
  { const m = new THREE.Mesh(new THREE.SphereGeometry(2.6, 12, 8), pickMat); m.position.set(vx, vy + 1, vz); m.userData.vent = true; s3.add(m); picks.push(m); }
  const card = root.querySelector(".scene-card");
  const RAMP = [[0, "#222a2f"], [25, "#3b3d36"], [50, "#5f5641"], [75, "#8f7346"], [110, "#b98049"], [210, "#d9934f"], [1010, "#f2c58a"]];  // the page's dark --seq
  const rampAt = (v) => { let i = RAMP.findIndex(([a]) => v < a); if (i === -1) return RAMP[RAMP.length - 1][1]; if (i === 0) return RAMP[0][1];
    const [a0, c0] = RAMP[i - 1], [a1, c1] = RAMP[i], t = (v - a0) / (a1 - a0), h = (c) => [1, 3, 5].map((k) => parseInt(c.slice(k, k + 2), 16)), x = h(c0), y = h(c1);
    return `rgb(${x.map((q, k) => Math.round(q + (y[k] - q) * t)).join(",")})`; };
  const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const showCard = () => {
    if (!card) return;
    if (!selected) { card.hidden = true; return; }
    const k = Math.floor(hour), hhmm = (i) => `${String((18 + i) % 24).padStart(2, "0")}:00`;
    if (selected === "vent") {
      card.innerHTML = `<button type="button" class="x" aria-label="Close">×</button><b>${esc(scene.vent.name)}</b><span class="role">Kīlauea summit vent</span><p>${esc(scene.episode || "")}</p>`;
    } else {
      const st = selected, vals = hours ? hours[st.id] : null, now = vals ? vals[k] : null;
      let spark = "";
      if (vals) {
        const W = 228, H = 64, bw = W / 18, mx = Math.max(100, ...vals.filter((v) => v != null)), y = (v) => H - 4 - (H - 10) * Math.sqrt(Math.max(v, 0) / mx);
        spark = `<svg viewBox="0 0 ${W} ${H + 14}" width="${W}" height="${H + 14}" aria-label="${esc(st.name)}, hourly SO2 from 18:00 to 11:00 HST">` +
          vals.map((v, i) => v == null ? "" : `<rect x="${(i * bw + 1).toFixed(1)}" y="${y(v).toFixed(1)}" width="${(bw - 2).toFixed(1)}" height="${(H - 4 - y(v)).toFixed(1)}" rx="1.5" fill="${rampAt(v)}"${i === k ? ' stroke="#f2f5f6" stroke-width="1.5"' : ""}/>`).join("") +
          `<line x1="0" x2="${W}" y1="${y(75).toFixed(1)}" y2="${y(75).toFixed(1)}" stroke="#f2f5f6" stroke-opacity=".55" stroke-dasharray="3 3"/>` +
          `<text x="${W}" y="${(y(75) - 3).toFixed(1)}" text-anchor="end" font-size="10" fill="#c3ccd1">75 ppb</text>` +
          `<text x="0" y="${H + 12}" font-size="10" fill="#9aa6ad">18:00</text><text x="${W}" y="${H + 12}" text-anchor="end" font-size="10" fill="#9aa6ad">11:00</text></svg>`;
      }
      const top = vals ? Math.max(...vals.filter((v) => v != null)) : null;
      card.innerHTML = `<button type="button" class="x" aria-label="Close">×</button><b>${esc(st.name)}</b><span class="role">${esc(st.role)}</span>` +
        (vals ? `<p class="now"><strong>${now == null ? "no value" : Math.round(now) + " ppb"}</strong> at ${hhmm(k)} HST. Highest hour of the night: ${Math.round(top)} ppb.</p>${spark}` : `<p>Measured hours appear after 12:00 HST tomorrow.</p>`);
    }
    card.hidden = false;
    card.querySelector(".x").onclick = () => { selected = null; showCard(); };
  };
  const select = (what) => {
    selected = what; showCard();
    if (what === "vent") flyTo(new THREE.Vector3(vx + 14, vy + 18, vz + 26), new THREE.Vector3(vx, vy, vz));
    else if (what) flyTo(new THREE.Vector3(what.x + 14, what.y + 16, what.z + 24), new THREE.Vector3(what.x, what.y + 0.8, what.z));
  };
  stations.forEach((st) => { st.label.addEventListener("click", () => select(st)); st.label.tabIndex = 0; st.label.addEventListener("keydown", (e) => { if (e.key === "Enter") select(st); }); });
  ventLabel.addEventListener("click", () => select("vent"));
  const ray = new THREE.Raycaster(), ndc = new THREE.Vector2(); let down = null;
  canvas.addEventListener("pointerdown", (e) => { down = [e.clientX, e.clientY]; });
  canvas.addEventListener("pointerup", (e) => {
    if (!down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 6) return;
    const r = canvas.getBoundingClientRect(); ndc.set((e.clientX - r.left) / r.width * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(ndc, camera); const hit = ray.intersectObjects(picks)[0];
    if (hit) select(hit.object.userData.vent ? "vent" : hit.object.userData.st);
  });
  canvas.addEventListener("pointermove", (e) => {
    const r = canvas.getBoundingClientRect(); ndc.set((e.clientX - r.left) / r.width * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(ndc, camera); canvas.style.cursor = ray.intersectObjects(picks).length ? "pointer" : "grab";
  });

  // State and drawing.
  let hour = opts.hour ?? 0, playing = false, last = performance.now(), visible = true, raf = 0;
  const hours = scene.hours;
  const setHour = (k) => {
    hour = Math.max(0, Math.min(17, k));
    stations.forEach((st) => {
      const v = hours ? hours[st.id]?.[Math.floor(hour)] : null;
      const span = st.label.querySelector(".v");
      const on = v != null;
      st.beam.visible = st.head.visible = st.halo.visible = st.ring.visible = on; st.mark.visible = !on;
      if (!on) { st.beamH = 0; span.textContent = hours ? "no value" : "not measured yet"; st.label.classList.remove("hot"); st.label._w = st.label._h = undefined; return; }
      const hk = 2.8 * Math.sqrt(Math.max(v, 0) / 75) + 0.2;   // square root of SO2: 75 ppb stands 3.0 km, 1,200 ppb 11.4 km
      st.beamH = hk; st.hot = v >= 75;
      st.color.set(LEVEL(v)); st.halo.material.color.copy(st.color); st.head.material.color.copy(st.color);
      st.beam.scale.set(1, hk, 1);
      st.head.position.set(st.x, st.y + hk, st.z); st.head.scale.setScalar(st.hot ? 1.5 : 1.0);
      const r = 0.9 + 0.55 * Math.sqrt(Math.max(v, 0) / 75); st.halo.scale.set(r * 2, r * 2, 1); st.halo.userData.r = r * 2;
      span.textContent = `${Math.round(v)} ppb${st.hot ? ", above 75" : ""}`;
      st.label.classList.toggle("hot", st.hot); st.label._w = st.label._h = undefined;
    });
    if (selected && selected !== "vent") showCard();
    opts.onHour?.(Math.floor(hour), windAt(hour));
  };

  const resize = () => {
    const present = root.classList.contains("present");
    const w = root.clientWidth, narrow = w < 560, h = present ? innerHeight : narrow ? Math.round(w * 0.9) : Math.round(Math.min(600, Math.max(340, w * (w < 900 ? 0.66 : 0.58))));
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2)); renderer.setSize(w, h, false);
    composer.setPixelRatio(Math.min(devicePixelRatio, 2)); composer.setSize(w, h); bloom.setSize(w, h);
    canvas.style.width = `${w}px`; canvas.style.height = `${h}px`; camera.aspect = w / h;
    const far = present ? 0.95 : narrow ? 1.12 : Math.min(1.45, Math.max(0.92, 1.62 / camera.aspect));
    if (narrow) home.pos.set(6, 60, 50); else home.pos.set(10 * far, 48 * far, 70 * far);
    if (!userMoved && !fly) { camera.position.copy(home.pos); controls.target.copy(home.target); }
    camera.updateProjectionMatrix(); controls.update();
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

  // The sky and the light follow the hour: dusk at 18:00, night, first light near 06:00, morning by 09:00.
  const C = (h) => new THREE.Color(h);
  const SKY = { dusk: [C(0x161b36), C(0x6e4c4a)], night: [C(0x0b1026), C(0x222a48)], dawn: [C(0x26304e), C(0xa9806a)], day: [C(0x48627a), C(0x8fa4b0)] };
  const lerpSky = (a, b, t) => [a[0].clone().lerp(b[0], t), a[1].clone().lerp(b[1], t)];
  const light = (k) => {
    let sky, day;
    if (k < 1) { sky = lerpSky(SKY.dusk, SKY.night, k); day = 0.25 * (1 - k); }
    else if (k < 11.5) { sky = SKY.night; day = 0; }
    else if (k < 13) { const t = (k - 11.5) / 1.5; sky = lerpSky(SKY.night, SKY.dawn, t); day = 0.3 * t; }
    else if (k < 15) { const t = (k - 13) / 2; sky = lerpSky(SKY.dawn, SKY.day, t); day = 0.3 + 0.6 * t; }
    else { sky = SKY.day; day = 0.9; }
    skyMat.uniforms.uTop.value.copy(sky[0]); skyMat.uniforms.uBottom.value.copy(sky[1]); FOG.copy(sky[1]).lerp(C(0x161b2c), 0.35);
    terrainMat.uniforms.uDay.value = day; renderer.toneMappingExposure = 1.35 - 0.15 * day;
    bloom.threshold = 0.5 + 0.35 * day; bloom.strength = 0.6 * (1 - 0.55 * day);
    pmat.uniforms.fade.value = wmat.uniforms.fade.value = 1 - 0.6 * day;
  };

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
        psize[i] = 1 + age[i] * 0.45;
        alpha[i] = Math.min(1, age[i] * 1.5) * (1 - age[i] / life) * 0.8 / Math.sqrt(psize[i]);
      }
      pgeo.attributes.position.needsUpdate = true; pgeo.attributes.alpha.needsUpdate = true; pgeo.attributes.psize.needsUpdate = true;
    }
    const t = now / 1000;
    if (introPending && visible) { introPending = false; camera.position.set(home.pos.x + 60, home.pos.y + 110, home.pos.z + 120); flyTo(home.pos, home.target, 2600); }
    if (fly) { const k = ease((now - fly.start) / fly.dur); camera.position.lerpVectors(fly.p0, fly.p1, k); controls.target.lerpVectors(fly.t0, fly.t1, k); if (k >= 1) fly = null; }
    else if (!userMoved && !reduce) { const off = home.pos.clone().sub(home.target), a = Math.sin(t * 0.11) * 0.16; off.applyAxisAngle(new THREE.Vector3(0, 1, 0), a); camera.position.copy(home.target).add(off); }
    controls.update();
    const dist = camera.position.distanceTo(controls.target);
    terrainMat.uniforms.uFogN.value = dist * 1.05; terrainMat.uniforms.uFogF.value = dist * 2.4;
    sea.material.uniforms.uFogN.value = dist * 0.9; sea.material.uniforms.uFogF.value = dist * 2.6;
    light(hour);
    if (w && !reduce) {
      const sp = Math.hypot(w.v[0], w.v[1]) || 1, ux = w.v[0] / sp, uz = w.v[1] / sp, len = 1.0 + sp * 0.55, kps = 3.6 / SECONDS_PER_HOUR;
      for (let i = 0; i < NW; i++) {
        wage[i] += dt; let x = wpos[i * 6] + w.v[0] * kps * dt, z = wpos[i * 6 + 2] + w.v[1] * kps * dt;
        if (wage[i] > wlife || Math.abs(x) > KW / 2 || Math.abs(z) > KH / 2) { wspawn(i); wage[i] = 0; x = wpos[i * 6]; z = wpos[i * 6 + 2]; }
        const y = Math.min(Math.max(heightAt(x, z), 0) + 0.9, lidY - 0.05);
        wpos.set([x, y, z, x - ux * len, y, z - uz * len], i * 6);
        const a = Math.min(1, wage[i] * 2) * Math.min(1, (wlife - wage[i]) * 2) * (heightAt(x, z) > 0.002 ? 0.3 : 0.12);
        walpha[i * 2] = a; walpha[i * 2 + 1] = 0;
      }
      wgeo.attributes.position.needsUpdate = true; wgeo.attributes.alpha.needsUpdate = true;
    }
    if (!reduce) stations.forEach((st) => { if (st.hot && st.halo.visible) { const k = 1 + 0.12 * Math.sin(t * 3.9); st.halo.scale.set(st.halo.userData.r * k, st.halo.userData.r * k, 1); } });
    composer.render();
    project(vx - 0.8, vy + 0.6, vz, ventLabel);
    stations.forEach((st) => project(st.x, st.y + (st.beam.visible ? st.beamH + 0.6 : 1.2), st.z, st.label));
    layout([...stations.map((st) => st.label), ventLabel]);
    if (visible && (!reduce || playing || fly || controls.enabled)) raf = requestAnimationFrame(step); else raf = 0;
  };
  const kick = () => { if (!raf) { last = performance.now(); raf = requestAnimationFrame(step); } };

  if (reduce && scene.wind.length) {                          // static drift lines instead of moving particles
    for (let i = 0; i < N; i++) {
      const t = (i % 100) / 100 * 8, lane = Math.floor(i / 100) - 8, w = windAt(Math.min(17, t));
      const side = [-w.v[1], w.v[0]], n = Math.hypot(...side) || 1;
      pp[i * 3] = vx + w.v[0] * 3.6 * t + side[0] / n * lane * 0.35; pp[i * 3 + 2] = vz + w.v[1] * 3.6 * t + side[1] / n * lane * 0.35;
      pp[i * 3 + 1] = Math.min(Math.max(heightAt(pp[i * 3], pp[i * 3 + 2]), 0) + 0.35, lidY - 0.1); psize[i] = 1 + t * 0.45; alpha[i] = 0.3 * (1 - t / 8) / psize[i];
    }
    pgeo.attributes.position.needsUpdate = true; pgeo.attributes.alpha.needsUpdate = true; pgeo.attributes.psize.needsUpdate = true;
  }

  if (reduce && scene.wind.length) {
    const w = windAt(0), sp = Math.hypot(w.v[0], w.v[1]) || 1, ux = w.v[0] / sp, uz = w.v[1] / sp;
    for (let i = 0; i < NW; i++) { const x = wpos[i * 6], z = wpos[i * 6 + 2], y = Math.max(heightAt(x, z), 0) + 0.9; wpos.set([x, y, z, x - ux * 1.6, y, z - uz * 1.6], i * 6); walpha[i * 2] = 0.22; }
    wgeo.attributes.position.needsUpdate = true; wgeo.attributes.alpha.needsUpdate = true;
  }
  new IntersectionObserver(([e]) => { visible = e.isIntersecting; if (visible) kick(); }).observe(root);
  addEventListener("resize", () => { resize(); [ventLabel, ...stations.map((st) => st.label)].forEach((el) => { el._w = el._h = undefined; }); kick(); });
  resize(); setHour(hour); kick();
  root._scene3d = { camera, controls, home, get fly() { return fly; }, get userMoved() { return userMoved; } };  // for inspection in the browser console
  root.addEventListener("keydown", (e) => {
    if (e.target !== root) return;
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") { e.preventDefault(); playing = false; setHour(Math.floor(hour) + (e.key === "ArrowRight" ? 1 : -1)); kick(); }
  });
  return {
    view: (name) => { userMoved = true; selected = null; showCard(); const [p, t] = views[name](); flyTo(p, t); },
    explore: (on) => { controls.enabled = on; canvas.style.touchAction = on ? "none" : ""; kick(); },
    get coarse() { return coarse; },
    setHour: (k) => { playing = false; setHour(k); kick(); },
    play: () => { if (hour >= 17) hour = 0; playing = true; kick(); opts.onPlay?.(true); },
    pause: () => { playing = false; opts.onPlay?.(false); },
    get playing() { return playing; },
  };
}
