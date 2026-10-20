/* 첫 진입·새로고침 인트로 (약 2.7초): 빨간 포스터 위로 SEE TRUTH / MOVE BRAVE 가 위아래에서 밀려 들어오고,
   가운데에 거친 망점 인쇄 같은 시세 차트 사진띠가 걸린 뒤, 거꾸로 빠지며 사이트가 드러난다.
   사이트 안에서 페이지를 옮길 때는 띄우지 않는다. 클릭·키 입력으로 건너뛴다. 움직임 줄이기 설정이면 띄우지 않는다.
   <head> 에서 실행해 본문이 먼저 그려지지 않게 html.intro 로 가린 뒤, 본문이 준비되면 덮개를 얹고 가림을 푼다. */
(() => {
  const nav = performance.getEntriesByType('navigation')[0];
  let inside = false;
  try { const from = new URL(document.referrer); inside = from.origin === location.origin && from.pathname.startsWith('/truth'); } catch { /* 외부·없음 */ }
  const want = (nav && nav.type === 'reload') || !inside;
  // 자동화 브라우저(게시 전 렌더 검사)는 건너뛴다 — 검사가 외부 글꼴·인트로 시간에 흔들리지 않게
  if (!want || navigator.webdriver || matchMedia('(prefers-reduced-motion: reduce)').matches) return;

  // 굵고 좁은 제목 글꼴 (Google Fonts). 늦으면 Impact 로 그린다
  const font = document.createElement('link');
  font.rel = 'stylesheet';
  font.href = 'https://fonts.googleapis.com/css2?family=Anton&display=block';
  const fontReady = new Promise(r => { font.onload = font.onerror = r; }).then(() => document.fonts.load('100px "Anton"'));
  document.head.append(font);

  const SIZE = .88; // 글자가 차지하는 비율(높이·폭)
  const root = document.documentElement;
  root.classList.add('intro');
  document.addEventListener('DOMContentLoaded', () => {
    let k = 0;
    const glyphs = text => [...text].map(ch => `<i style="--k:${k++}">${ch === ' ' ? '&nbsp;' : ch}</i>`).join('');
    // 가로 화면은 반쪽에 한 줄(SEE TRUTH), 세로 화면은 두 줄(SEE / TRUTH) — 글자가 너무 얇아지지 않게
    const portrait = innerWidth < innerHeight * .9;
    const rows = words => (portrait ? words : [words.join(' ')]).map(w => `<span>${glyphs(w)}</span>`).join('');
    const el = document.createElement('div');
    el.id = 'intro';
    el.setAttribute('aria-hidden', 'true');
    el.innerHTML = `
      <div class="line up">${rows(['SEE', 'TRUTH'])}</div>
      <div class="line down">${rows(['MOVE', 'BRAVE'])}</div>
      <div class="photo"><canvas></canvas></div>
      <div class="credit"><img src="logo.png" alt="">ETF ORCA<small>· 국장 세력 판, 언제까지 당할래?</small></div>`;
    document.body.append(el);
    root.classList.remove('intro');

    // 줄마다 높이를 채우는 크기로 쓰고, 화면 폭에 맞게 가로로만 늘리거나 줄인다(포스터처럼 끝에서 끝까지)
    const fit = () => {
      const probe = document.createElement('canvas').getContext('2d');
      probe.font = '100px "Anton", Impact, sans-serif';
      const cap = probe.measureText('H').actualBoundingBoxAscent / 100 || .8; // 글꼴 크기 대비 대문자 높이
      el.querySelectorAll('.line').forEach(line => {
        const rows = line.children, gap = rows.length > 1 ? line.clientHeight * .03 : 0;
        line.style.setProperty('--row-gap', `${gap}px`);
        for (const span of rows) {
          // 줄 높이·화면 폭을 꽉 채우지 않고 조금 남긴다(SIZE) — 꽉 채우면 부담스럽다
          span.style.setProperty('--fs', `${(line.clientHeight - gap * (rows.length - 1)) / rows.length / cap * SIZE}px`);
          span.style.setProperty('--sx', `${innerWidth * SIZE / span.scrollWidth}`);
        }
      });
    };
    let started = false;
    const start = () => {
      if (started) return;
      started = true;
      fit();
      halftone(el.querySelector('canvas'));
      el.classList.add('go');
      setTimeout(leave, 2300);
    };
    const leave = () => { start(); el.classList.add('leaving'); };
    Promise.race([fontReady, new Promise(r => setTimeout(r, 900))]).then(start, start);
    fontReady.then(fit, () => {});
    addEventListener('resize', fit);
    el.addEventListener('animationend', e => { if (e.animationName === 'intro-out') el.remove(); });
    el.addEventListener('click', leave);
    addEventListener('keydown', leave, {once: true});
    setTimeout(() => el.remove(), 5500); // 애니메이션 이벤트를 못 받아도 화면을 가리지 않게
  });

  /* 사진띠: 급등 뒤 폭락하는 캔들 차트를 그린 다음, 신문 인쇄 같은 망점과 잡티로 바꾼다(외부 사진을 쓰지 않는다). */
  function halftone(canvas) {
    const w = Math.min(1600, canvas.clientWidth), h = Math.round(w * canvas.clientHeight / canvas.clientWidth);
    canvas.width = w;
    canvas.height = h;
    let seed = 7;
    const rand = () => (seed = (seed * 16807) % 2147483647) / 2147483647;

    const src = document.createElement('canvas');
    src.width = w;
    src.height = h;
    const g = src.getContext('2d');
    const paper = g.createLinearGradient(0, 0, w, h);
    paper.addColorStop(0, '#d9d5cc');
    paper.addColorStop(.55, '#a8a49c');
    paper.addColorStop(1, '#6f6c66');
    g.fillStyle = paper;
    g.fillRect(0, 0, w, h);
    g.strokeStyle = 'rgba(0,0,0,.18)';
    g.lineWidth = Math.max(1, h / 220);
    for (let y = h * .12; y < h; y += h / 6) { g.beginPath(); g.moveTo(0, y); g.lineTo(w, y); g.stroke(); }

    const n = 38, step = w / n, body = step * .62;
    const prices = [];
    let p = .7;
    for (let i = 0; i < n; i++) {
      const pump = i > 12 && i < 23 ? -.06 : 0, dump = i >= 23 && i < 28 ? .13 : 0; // 화면 좌표라 위가 작다
      p = Math.min(.9, Math.max(.08, p + (rand() - .5) * .07 + pump + dump));
      prices.push(p);
    }
    let prev = prices[0];
    prices.forEach((close, i) => {
      const x = i * step + step / 2, open = prev, hi = Math.min(open, close) - rand() * .07, lo = Math.max(open, close) + rand() * .07;
      g.strokeStyle = g.fillStyle = close <= open ? '#141414' : '#5a5a5a';
      g.lineWidth = Math.max(1, step * .09);
      g.beginPath(); g.moveTo(x, hi * h); g.lineTo(x, lo * h); g.stroke();
      g.fillRect(x - body / 2, Math.min(open, close) * h, body, Math.max(h * .012, Math.abs(close - open) * h));
      prev = close;
    });
    g.strokeStyle = 'rgba(0,0,0,.75)';
    g.lineWidth = Math.max(2, h / 90);
    g.beginPath();
    prices.forEach((v, i) => {
      const avg = prices.slice(Math.max(0, i - 4), i + 1).reduce((a, b) => a + b, 0) / Math.min(i + 1, 5);
      if (i) g.lineTo(i * step + step / 2, avg * h); else g.moveTo(step / 2, avg * h);
    });
    g.stroke();

    const pixels = g.getImageData(0, 0, w, h).data;
    const out = canvas.getContext('2d');
    out.fillStyle = '#d6d2c9';
    out.fillRect(0, 0, w, h);
    out.fillStyle = '#0d0d0d';
    const cell = Math.max(4, Math.round(h / 64));
    for (let y = 0; y < h; y += cell) {
      for (let x = (y / cell) % 2 ? cell / 2 : 0; x < w; x += cell) {
        const i = (Math.min(h - 1, Math.round(y)) * w + Math.min(w - 1, Math.round(x))) * 4;
        const dark = 1 - (pixels[i] * .3 + pixels[i + 1] * .59 + pixels[i + 2] * .11) / 255;
        const r = cell * .72 * Math.sqrt(Math.max(0, dark + (rand() - .5) * .12));
        if (r > .35) { out.beginPath(); out.arc(x, y, r, 0, Math.PI * 2); out.fill(); }
      }
    }
    for (let i = 0; i < w * h / 90; i++) { // 인쇄 잡티
      out.fillStyle = rand() < .5 ? 'rgba(0,0,0,.35)' : 'rgba(255,255,255,.35)';
      out.fillRect(rand() * w, rand() * h, 1 + rand() * 1.5, 1 + rand() * 1.5);
    }
  }
})();
