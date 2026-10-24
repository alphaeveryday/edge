import * as THREE from 'three';
export function startGraph(canvas) {
  var GL = null, GLspeed = 3.2, GLraf = 0;
  function startGL() {
    if (!GL) {
      var el = canvas, w = el.clientWidth || 360, h = el.clientHeight || 250;
      var r = new THREE.WebGLRenderer({ canvas: el, antialias: true, alpha: true });
      r.setPixelRatio(Math.min(2, window.devicePixelRatio || 1)); r.setSize(w, h, false);
      var scene = new THREE.Scene(), cam = new THREE.PerspectiveCamera(44, w / h, 0.1, 100); cam.position.set(0, 0, 9.4);
      var grp = new THREE.Group(); scene.add(grp);
      var N = 54, P = [], seed = 11;
      var rnd = function () { seed = seed * 16807 % 2147483647; return seed / 2147483647; };
      for (var i = 0; i < N; i++) {                      // 구 껍질 위에 고르게, 조금씩 흩어서
        var y = 1 - i / (N - 1) * 2, rad = Math.sqrt(1 - y * y), th = i * 2.399963, R = 2.5 + rnd() * 1.1;
        P.push(new THREE.Vector3(Math.cos(th) * rad * R, y * R * 0.82, Math.sin(th) * rad * R));
      }
      var E = [], nb = P.map(function () { return []; });
      P.forEach(function (p, a) {                         // 가까운 점 셋과 잇는다
        P.map(function (q, b) { return [p.distanceTo(q), b]; }).sort(function (x, z) { return x[0] - z[0]; }).slice(1, 4).forEach(function (d) {
          var b = d[1]; if (nb[a].indexOf(b) < 0) { nb[a].push(b); nb[b].push(a); E.push([a, b]); }
        });
      });
      var dot = document.createElement('canvas'); dot.width = dot.height = 64;
      var g2 = dot.getContext('2d'), gr = g2.createRadialGradient(32, 32, 0, 32, 32, 32);
      gr.addColorStop(0, 'rgba(255,255,255,1)'); gr.addColorStop(0.35, 'rgba(255,255,255,.85)'); gr.addColorStop(1, 'rgba(255,255,255,0)');
      g2.fillStyle = gr; g2.fillRect(0, 0, 64, 64);
      var tex = new THREE.CanvasTexture(dot);
      var pg = new THREE.BufferGeometry().setFromPoints(P), pc = new Float32Array(N * 3);
      pg.setAttribute('color', new THREE.BufferAttribute(pc, 3));
      grp.add(new THREE.Points(pg, new THREE.PointsMaterial({ size: 0.34, map: tex, vertexColors: true, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending })));
      var lp = new Float32Array(E.length * 6), lc = new Float32Array(E.length * 6);
      E.forEach(function (e, k) { P[e[0]].toArray(lp, k * 6); P[e[1]].toArray(lp, k * 6 + 3); });
      var lg = new THREE.BufferGeometry(); lg.setAttribute('position', new THREE.BufferAttribute(lp, 3)); lg.setAttribute('color', new THREE.BufferAttribute(lc, 3));
      grp.add(new THREE.LineSegments(lg, new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.9, blending: THREE.AdditiveBlending, depthWrite: false })));
      var halo = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, color: 0x9fd0ff, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending }));
      halo.scale.set(1.5, 1.5, 1); grp.add(halo);
      GL = { r: r, scene: scene, cam: cam, grp: grp, N: N, P: P, E: E, nb: nb, pg: pg, lg: lg, pc: pc, lc: lc, halo: halo,
             heat: new Float32Array(N), eheat: new Float32Array(E.length), pick: new Float32Array(N), at: 0, from: 0, hop: 0, hops: 0, t: 0, last: 0 };
    }
    if (!GLraf) GLraf = requestAnimationFrame(frame);
  }
  function frame(now) {
    var g = GL; GLraf = 0;
    if (!GL) return;                // 다른 화면에서는 쉰다
    var dt = Math.min(0.05, (now - (g.last || now)) / 1000); g.last = now; g.t += dt;
    var calm = matchMedia('(prefers-reduced-motion: reduce)').matches;
    g.hop += dt * GLspeed * 3.4;
    if (g.hop >= 1 && !calm) {                           // 다음 점으로: 덜 가 본 이웃을 고른다
      g.hop = 0; g.hops++;
      var opts = g.nb[g.at].slice().sort(function (a, b) { return g.heat[a] - g.heat[b]; });
      var next = opts[Math.random() < 0.7 ? 0 : Math.floor(Math.random() * opts.length)];
      g.E.forEach(function (e, k) { if ((e[0] === g.at && e[1] === next) || (e[1] === g.at && e[0] === next)) g.eheat[k] = 1; });
      g.from = g.at; g.at = next; g.heat[next] = 1;
      if (g.hops % 5 === 0) g.pick[next] = 1;           // 다섯 걸음마다 한 점을 '고른 것'으로 남긴다
    }
    for (var i = 0; i < g.N; i++) {
      g.heat[i] *= 1 - dt * 0.55; g.pick[i] *= 1 - dt * 0.07;
      var h = Math.max(g.heat[i], g.pick[i] * 0.9), p = g.pick[i];
      g.pc[i * 3] = 0.16 + h * 0.5 + p * 0.25; g.pc[i * 3 + 1] = 0.22 + h * 0.6; g.pc[i * 3 + 2] = 0.5 + h * 0.5;
    }
    for (var k = 0; k < g.E.length; k++) {
      g.eheat[k] *= 1 - dt * 0.8;
      var c = 0.07 + g.eheat[k] * 0.75;
      for (var j = 0; j < 2; j++) { g.lc[k * 6 + j * 3] = c * 0.5; g.lc[k * 6 + j * 3 + 1] = c * 0.72; g.lc[k * 6 + j * 3 + 2] = c * 1.25; }
    }
    g.pg.attributes.color.needsUpdate = true; g.lg.attributes.color.needsUpdate = true;
    g.halo.position.lerpVectors(g.P[g.from], g.P[g.at], Math.min(1, g.hop));
    g.grp.rotation.y += dt * 0.16 * (calm ? 0 : 1 + (GLspeed - 1) * 0.6); g.grp.rotation.x = Math.sin(g.t * 0.3) * 0.16;
    g.r.render(g.scene, g.cam);
    if (!calm) GLraf = requestAnimationFrame(frame);
  }


  try { startGL(); } catch { return () => {}; }
  const resize = new ResizeObserver(() => { const w = canvas.clientWidth || 360, h = canvas.clientHeight || 250; GL.r.setSize(w, h, false); GL.cam.aspect = w / h; GL.cam.updateProjectionMatrix(); if (!GLraf) GL.r.render(GL.scene, GL.cam); });
  resize.observe(canvas);
  return () => { cancelAnimationFrame(GLraf); resize.disconnect(); GL.grp.traverse(object => { object.geometry?.dispose(); if (object.material) { object.material.map?.dispose(); object.material.dispose(); } }); GL.r.dispose(); GL = null; };
}
