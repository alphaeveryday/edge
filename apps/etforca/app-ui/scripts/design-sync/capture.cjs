// design-sync 2단계: 웹 빌드(<out>/app)를 로컬에서 띄워 화면을 라이트·다크로 찍고 보기 페이지를 만든다.
// 실행: NODE_PATH=<playwright-core 가 있는 node_modules> node capture.cjs <out> <커밋> [이름…]
// 결과: <out>/screens/{light,dark}/<이름>.png, <out>/screens/index.html, <out>/index.html
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { chromium } = require('playwright-core');
const SCREENS = require('./screens.cjs');

const [outArg, commit, ...only] = process.argv.slice(2);
const out = outArg && path.resolve(outArg);
if (!outArg || !commit) throw new Error('사용법: node capture.cjs <out> <커밋> [이름…]');
const W = 402, H = 874;
const MOCK_LOGIN = ['jisoo.kim@gmail.com', 'password']; // src/api/mock/index.ts 의 ACCOUNTS

// design.etforca.com 의 CloudFront 함수(infra/terraform/envs/dev/design-site.js)와 같은 경로 규칙
function resolve(uri) {
  const last = uri.split('/').pop();
  if (uri === '/app' || (uri.startsWith('/app/') && !last.includes('.'))) return '/app/index.html';
  if (uri.endsWith('/')) return uri + 'index.html';
  if (!last.includes('.')) return uri + '/index.html';
  return uri;
}
const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css', '.png': 'image/png', '.ttf': 'font/ttf', '.woff2': 'font/woff2', '.json': 'application/json', '.ico': 'image/x-icon', '.svg': 'image/svg+xml' };
function serve() {
  const server = http.createServer((req, res) => {
    const file = path.join(out, decodeURIComponent(resolve(new URL(req.url, 'http://x').pathname)));
    if (!file.startsWith(out) || !fs.existsSync(file)) { res.writeHead(404); res.end(); return; }
    res.writeHead(200, { 'content-type': TYPES[path.extname(file)] ?? 'application/octet-stream' });
    fs.createReadStream(file).pipe(res);
  });
  return new Promise((ok) => server.listen(0, '127.0.0.1', () => ok(server)));
}

async function settle(page) {
  await page.waitForLoadState('networkidle');
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(800); // mock 지연과 화면 전환 애니메이션
}

async function run(step, page) {
  if (step.click) await page.getByText(step.click, { exact: true }).first().click();
  if (step.label) await page.getByLabel(step.label, { exact: true }).first().click();
  if (step.fill) await page.getByPlaceholder(step.fill[0]).fill(step.fill[1]);
  if (step.wait) await page.waitForTimeout(step.wait);
}

async function shoot(browser, base, s, scheme) {
  const ctx = await browser.newContext({ viewport: { width: W, height: H }, deviceScaleFactor: 2, colorScheme: scheme, locale: 'ko-KR', timezoneId: 'Asia/Seoul' });
  const state = s.state ?? 'guest';
  await ctx.addInitScript((st) => {
    if (sessionStorage.getItem('design-sync-init')) return;
    sessionStorage.setItem('design-sync-init', '1');
    localStorage.clear();
    if (st !== 'fresh') { localStorage.setItem('etforca.onboarded', '1'); localStorage.setItem('etforca.disclaimed', '1'); }
  }, state);
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  if (state === 'member') {
    await page.goto(base + '/app/login');
    await settle(page);
    await page.getByPlaceholder('이메일').fill(MOCK_LOGIN[0]);
    await page.getByPlaceholder('비밀번호').fill(MOCK_LOGIN[1]);
    await page.getByText('로그인', { exact: true }).last().click();
    await page.waitForTimeout(2500); // '로그인했어요' 알림이 사라질 때까지
  } else {
    await page.goto(base + '/app' + encodeURI(s.route));
  }
  await settle(page);
  for (const step of s.steps ?? []) { await run(step, page); await settle(page); }
  await page.evaluate(() => document.activeElement?.blur()); // 웹 입력칸의 초점 테두리는 앱에 없다
  const file = path.join(out, 'screens', scheme, s.name + '.png');
  await page.screenshot({ path: file });
  const url = decodeURI(new URL(page.url()).pathname.replace(/^\/app/, '')) || '/';
  await ctx.close();
  return { url, errors };
}

const esc = (v) => String(v).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);

function screensPage(rows, meta) {
  const groups = [...new Set(rows.map((r) => r.group))];
  const card = (r) => `
    <figure id="${esc(r.name)}">
      <div class="pair">
        <a href="/?route=${encodeURIComponent(r.route)}&scheme=light" title="라이트로 열기"><img loading="lazy" src="light/${esc(r.name)}.png" alt="${esc(r.title)} 라이트"></a>
        <a href="/?route=${encodeURIComponent(r.route)}&scheme=dark" title="다크로 열기"><img loading="lazy" src="dark/${esc(r.name)}.png" alt="${esc(r.title)} 다크"></a>
      </div>
      <figcaption><b>${esc(r.title)}</b> <code>${esc(r.route)}</code>${r.state === 'member' ? ' <span class="tag">회원</span>' : ''}${r.warn ? ` <span class="warn" title="${esc(r.warn)}">확인 필요</span>` : ''}</figcaption>
    </figure>`;
  return `<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ETF Orca 화면 모음</title><style>
body{margin:0;font:14px/1.5 -apple-system,"Pretendard",system-ui,sans-serif;background:#f4f4f6;color:#131318}
header{position:sticky;top:0;background:#fff;border-bottom:1px solid #e4e4ea;padding:12px 24px;display:flex;gap:16px;align-items:baseline;flex-wrap:wrap;z-index:1}
header h1{font-size:18px;margin:0}header a{color:#3D34E0}nav a{margin-right:10px;color:#555;text-decoration:none}
main{padding:8px 24px 48px}h2{font-size:16px;margin:28px 0 12px}
.grid{display:flex;flex-wrap:wrap;gap:24px}figure{margin:0;background:#fff;border:1px solid #e4e4ea;border-radius:12px;padding:12px}
.pair{display:flex;gap:8px}.pair img{width:201px;height:437px;border-radius:8px;border:1px solid #e4e4ea;display:block}
figcaption{margin-top:8px;max-width:410px}code{color:#666;font-size:12px}.tag{font-size:11px;background:#eeedfc;color:#3D34E0;border-radius:4px;padding:1px 5px}
.warn{font-size:11px;background:#fdecec;color:#c62828;border-radius:4px;padding:1px 5px}.meta{color:#777;font-size:12px}
</style></head><body>
<header><h1>ETF Orca 화면 모음</h1><a href="/">← 앱 눌러 보기</a>
<nav>${groups.map((g) => `<a href="#g-${esc(g)}">${esc(g)}</a>`).join('')}</nav>
<span class="meta">앱 코드 ${esc(meta.commit)} · ${esc(meta.at)} · mock 데이터 · 왼쪽 라이트, 오른쪽 다크 · 그림을 누르면 그 화면을 앱으로 열어요</span></header>
<main>${groups.map((g) => `<h2 id="g-${esc(g)}">${esc(g)}</h2><div class="grid">${rows.filter((r) => r.group === g).map(card).join('')}</div>`).join('')}</main>
</body></html>`;
}

function phonePage(meta) {
  return `<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ETF Orca 앱 디자인</title><style>
body{margin:0;font:14px/1.5 -apple-system,"Pretendard",system-ui,sans-serif;background:#f4f4f6;color:#131318;display:flex;justify-content:center;gap:40px;padding:32px;flex-wrap:wrap}
.phone{width:${W}px;height:${H}px;border-radius:44px;border:10px solid #131318;overflow:hidden;background:#000;box-shadow:0 20px 60px rgba(0,0,0,.18)}
iframe{width:${W}px;height:${H}px;border:0;display:block}
aside{max-width:300px}h1{font-size:20px;margin:8px 0 4px}.meta{color:#777;font-size:12px}
button{font:inherit;padding:8px 14px;border-radius:10px;border:1px solid #d4d4dc;background:#fff;cursor:pointer;margin:0 6px 6px 0}button.on{background:#131318;color:#fff;border-color:#131318}
a{color:#3D34E0}ul{padding-left:18px}
</style></head><body>
<div class="phone"><iframe id="app" title="ETF Orca"></iframe></div>
<aside>
<h1>ETF Orca 앱 디자인</h1>
<p class="meta">앱 코드 ${esc(meta.commit)}을 웹으로 빌드했어요 · ${esc(meta.at)}<br>데이터는 mock이에요. 로그인은 ${esc(MOCK_LOGIN[0])} / ${esc(MOCK_LOGIN[1])}</p>
<p><button data-s="light">라이트</button><button data-s="dark">다크</button><button data-s="system">기기 설정</button></p>
<p><button id="home">처음 화면</button><button id="reset">첫 실행으로</button></p>
<p><a href="/screens/">화면 모음(라이트·다크 한 장에) →</a></p>
<p class="meta">웹 빌드라 실제 휴대폰과 다른 곳이 있어요: 상태 표시줄이 없고, 카카오·애플 로그인과 푸시는 동작하지 않아요.</p>
</aside>
<script>
const f=document.getElementById('app'),q=new URLSearchParams(location.search);
const mark=()=>{const s=localStorage.getItem('etforca.theme')||'system';document.querySelectorAll('[data-s]').forEach(b=>b.classList.toggle('on',b.dataset.s===s))};
document.querySelectorAll('[data-s]').forEach(b=>b.onclick=()=>{localStorage.setItem('etforca.theme',b.dataset.s);mark();f.contentWindow.location.reload()});
document.getElementById('home').onclick=()=>{f.src='/app/'};
document.getElementById('reset').onclick=()=>{['etforca.onboarded','etforca.disclaimed'].forEach(k=>localStorage.removeItem(k));f.src='/app/'};
if(q.get('scheme'))localStorage.setItem('etforca.theme',q.get('scheme'));
if(q.get('route')){localStorage.setItem('etforca.onboarded','1');localStorage.setItem('etforca.disclaimed','1')}
mark();f.src='/app'+(q.get('route')||'/');
</script>
</body></html>`;
}

(async () => {
  const server = await serve();
  const base = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch();
  for (const scheme of ['light', 'dark']) fs.mkdirSync(path.join(out, 'screens', scheme), { recursive: true });
  const list = only.length ? SCREENS.filter((s) => only.includes(s.name)) : SCREENS;
  const rows = [];
  let failed = 0;
  for (const s of list) {
    const row = { ...s };
    for (const scheme of ['light', 'dark']) {
      try {
        const { url, errors } = await shoot(browser, base, s, scheme);
        const problems = [...errors];
        if (url !== s.route) problems.push(`${s.route} 대신 ${url} 이 열렸어요`);
        if (problems.length) row.warn = problems.join(' / ');
        console.log(`${problems.length ? '!' : 'ok'} ${scheme} ${s.name}${problems.length ? ' — ' + problems.join(' / ') : ''}`);
      } catch (e) {
        failed++;
        row.warn = e.message.split('\n')[0];
        console.log(`FAIL ${scheme} ${s.name} — ${row.warn}`);
      }
    }
    rows.push(row);
  }
  await browser.close();
  server.close();
  const meta = { commit, at: new Date().toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' }) };
  fs.writeFileSync(path.join(out, 'screens', 'index.html'), screensPage(rows, meta));
  fs.writeFileSync(path.join(out, 'index.html'), phonePage(meta));
  console.log(`화면 ${rows.length}개, 실패 ${failed}`);
  if (failed) process.exitCode = 1;
})();
