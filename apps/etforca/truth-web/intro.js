/* 첫 진입·새로고침 인트로 (약 2.5초): 스위스 포스터처럼 가로 막대가 그어지고 빨간 문구가 화면 폭을 채운 뒤, 막대가 걷히며 사이트가 드러난다.
   사이트 안에서 페이지를 옮길 때는 띄우지 않는다. 클릭·키 입력으로 건너뛴다. 움직임 줄이기 설정이면 띄우지 않는다.
   <head> 에서 실행해 본문이 먼저 그려지지 않게 html.intro 로 가린 뒤, 본문이 준비되면 덮개를 얹고 가림을 푼다. */
(() => {
  const nav = performance.getEntriesByType('navigation')[0];
  let inside = false;
  try { const from = new URL(document.referrer); inside = from.origin === location.origin && from.pathname.startsWith('/truth'); } catch { /* 외부·없음 */ }
  const want = (nav && nav.type === 'reload') || !inside;
  if (!want || matchMedia('(prefers-reduced-motion: reduce)').matches) return;

  // 막대 길이 순서 (포스터의 리듬): f 전체 · a 81% · b 62% · r 오른쪽 기준
  const TOP = ['a', 'r', 'f', 'b', 'f', 'a', 'r', 'f', 'b', 'f'];
  const BOTTOM = ['f', 'b', 'f', 'a', 'r', 'f', 'b'];
  const today = new Intl.DateTimeFormat('ko-KR', {timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit', weekday: 'short'})
    .format(new Date());

  const root = document.documentElement;
  root.classList.add('intro');
  document.addEventListener('DOMContentLoaded', () => {
    let i = 0;
    const bars = (kinds, toBig) => kinds.map((k, n) => {
      const d = toBig ? kinds.length - n : n + 1; // 걷힐 때는 문구에서 먼 줄부터
      return `<i class="bar ${k}${k !== 'f' && i % 2 ? ' s' : ''}" style="--i:${i++};--d:${d}"></i>`;
    }).join('');
    const el = document.createElement('div');
    el.id = 'intro';
    el.setAttribute('aria-hidden', 'true');
    el.innerHTML = `
      <div class="rows upper">${bars(TOP, true)}</div>
      <div class="info">
        <div class="brand"><img src="logo.png" alt=""><div><b>ETF ORCA</b>투자정보 기만 검증</div></div>
        <div class="when"><b>${today}</b>매시 갱신 · etforca.com/truth</div>
        <div><b>국장 세력 판, 언제까지 당할래?</b>진짜와 가짜, 우리가 검증해 줄게.</div>
      </div>
      <div class="big">${(innerWidth < innerHeight * .75 ? ['언제까지', '당할래?'] : ['언제까지 당할래?'])
        .map(line => `<span>${line}</span>`).join('')}</div>
      <div class="rows">${bars(BOTTOM, false)}</div>`;
    document.body.append(el);
    root.classList.remove('intro');

    // 빨간 문구를 줄마다 화면 폭에 꽉 맞춘다(세로 화면은 두 줄). 글꼴이 늦게 와도 다시 맞춘다
    const fit = () => el.querySelectorAll('.big span').forEach(line => {
      const pad = parseFloat(getComputedStyle(line).paddingLeft) * 2;
      line.style.setProperty('--fit', '100px');
      line.style.setProperty('--fit', `${100 * (innerWidth - pad) / (line.scrollWidth - pad)}px`);
    });
    let started = false;
    const start = () => {
      if (started) return;
      started = true;
      fit();
      el.classList.add('go');
      setTimeout(leave, 2000);
    };
    const leave = () => { start(); el.classList.add('leaving'); };
    Promise.race([document.fonts.ready, new Promise(r => setTimeout(r, 500))]).then(start);
    document.fonts.ready.then(fit);
    addEventListener('resize', fit);
    el.addEventListener('animationend', e => { if (e.animationName === 'intro-out') el.remove(); });
    el.addEventListener('click', leave);
    addEventListener('keydown', leave, {once: true});
    setTimeout(() => el.remove(), 5000); // 애니메이션 이벤트를 못 받아도 화면을 가리지 않게
  });
})();
