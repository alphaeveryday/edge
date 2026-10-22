// 세 페이지 공용 헬퍼. 데이터는 에이전트가 게시하는 data/index.json(요약)·data/cases/<id>.json(상세)이다.
const ARROW = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M7 17 17 7M8 7h9v9"/></svg>';
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const safeUrl = u => /^https?:\/\//i.test(u || '') ? esc(u) : '#';
const img = u => u ? `<img src="${safeUrl(u)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()">` : '';
const ext = u => `href="${safeUrl(u)}" target="_blank" rel="noopener noreferrer"`;
const issueHref = c => `issue?id=${encodeURIComponent(c.id)}`;  // 확장자 없는 주소(배포가 확장자 없는 사본도 올린다)
/* 훑어 읽는 자리(목록·카드·히어로)의 이름: 짧은 제목, 없으면 공개 첫 문장 */
const titleOf = c => c.title || c.headline[0];

/* 방법론(docs/design/truth-investigation-methodology.md)의 용어를 그대로 쓴다 */
const TYPES = {
  fabricated: '없는 말 지어냄', concealed: '알고도 숨김',
  deflected: '불리하면 말 돌림', disowned: '모른 척 발 뺌',
  excused: '억지로 갖다 붙임', blamed: '속은 사람 탓함',
};
const VERDICTS = {confirmed: '확인됨', suspected: '의심', refuted: '아님', insufficient: '자료 부족', not_applicable: '해당 없음'};
const typeNames = c => (c.types || []).map(t => TYPES[t] || t).join(' · ');

/* 파급력 가치: impact(단위 억원, 0~100). 조사 우선순위이며 증거가 아니다 */
const MAX_VALUE = 100;
const won = v => v >= 1 ? `${+v.toFixed(1)}억원` : v > 0 ? `${Math.round(v * 10000).toLocaleString()}만원` : '0원';
const vbar = v => `<div class="vbar" role="img" aria-label="파급력 가치 ${won(v)} (최대 100억원)"><i style="width:${Math.min(100, v / MAX_VALUE * 100)}%"></i></div>`;
/* 기록일→확인일 걸린 기간. 한 달 미만은 일 단위로 적는다(반올림해 '1개월'로 부풀리지 않는다) */
const elapsed = (a, b) => { const d = Math.round((Date.parse(b) - Date.parse(a)) / 864e5); return d < 30 ? `${d}일` : `${Math.round(d / 30.44)}개월`; };

/* 결과 표시. prior = 이 서비스가 확인일보다 먼저 사건으로 기록했는가.
   먼저 기록한 사건만 '적중·빗나감'이고, 확인 뒤에 정리한 사건은 '밝혀짐·해소'다. */
const prior = c => !!c.resolvedAt && c.flaggedAt < c.resolvedAt;
const resultLabel = c => c.status === 'pending' ? '판단 중'
  : c.status === 'confirmed' ? (prior(c) ? '✓ 적중' : '✓ 밝혀짐')
  : (prior(c) ? '빗나감' : '해소');
const sticker = c => `<i class="sticker s-${esc(c.status)}">${resultLabel(c)}</i>`;

const yearOf = c => +c.resolvedAt.slice(0, 4);
const pendingOf = cases => cases.filter(c => c.status === 'pending').sort((a, b) => b.impact - a.impact);
const resolvedOf = cases => cases.filter(c => c.status !== 'pending').sort((a, b) => b.resolvedAt.localeCompare(a.resolvedAt));

/* 성적: 확인된 거짓 = 공식 판단으로 행위가 확인된 사건 수.
   적중률 = 먼저 기록한 사건 중 확인된 비율. 그런 사건이 아직 없으면 집계 전으로 표시한다. */
function scoreOf(cases){
  const done = cases.filter(c => c.status !== 'pending');
  const ahead = done.filter(prior);
  const hits = ahead.filter(c => c.status === 'confirmed').length;
  return {
    confirmed: done.filter(c => c.status === 'confirmed').length,
    refuted: done.filter(c => c.status === 'refuted').length,
    rate: ahead.length ? Math.round(hits / ahead.length * 100) : null,
  };
}
const rateText = s => s.rate === null ? '적중률 집계 전' : `적중률 ${s.rate}%`;
/* 홈 아래 표는 위 화면에 다 보이지 않을 때만 보여 준다(같은 사건을 두 번 보여 주지 않는다) */
const ABOVE_PENDING = 4, ABOVE_RESOLVED = 5;

/* 목록 표: 홈 아래 목록과 list.html 이 같은 행을 쓴다 */
const ti = c => `<div class="ti"><div class="ph-img">${img(c.cover)}</div><div><b>${esc(titleOf(c))}</b>${titleOf(c).includes(c.subject) ? '' : `<small>${esc(c.subject)}</small>`}</div></div>`;
const no = i => `<span class="no"><span>${i + 1}</span>${ARROW}</span>`;

const PENDING_HEAD = '<div class="trow thead"><span class="no">#</span><span>사건</span><span>유형</span><span>파급력 가치 <small>(최대 100억원)</small></span><span class="date">기록일</span></div>';
const pendingRow = (c, i) => `
  <a class="trow" href="${issueHref(c)}">${no(i)}${ti(c)}
    <span class="type">${esc(typeNames(c))}</span>
    <div class="st"><b>${won(c.impact)}</b>${vbar(c.impact)}</div>
    <span class="date">${esc(c.flaggedAt)}</span>
  </a>`;

const RESOLVED_HEAD = '<div class="trow r thead"><span class="no">#</span><span>사건</span><span>결과</span><span>유형</span><span class="date">기록일 → 확인일</span><span class="span">걸린 기간</span></div>';
const resolvedRow = (c, i) => `
  <a class="trow r${c.status === 'refuted' ? ' miss' : ''}" href="${issueHref(c)}">${no(i)}${ti(c)}
    <span class="res">${sticker(c)}</span>
    <span class="type">${esc(typeNames(c))}</span>
    <span class="date">${esc(c.flaggedAt)} → ${esc(c.resolvedAt)}</span>
    <span class="span">${prior(c) ? elapsed(c.flaggedAt, c.resolvedAt) : '사후 정리'}</span>
  </a>`;

/* 최상단 바: 로고·슬로건 | 오늘의 급등주 · 다른 서비스 버튼. 모든 페이지 공용(독립 서비스 화면 today 는 제 머리를 쓴다) */
if (!document.body.hasAttribute('data-standalone')) document.querySelector('.window').insertAdjacentHTML('beforebegin', `
  <header class="topbar">
    <a class="logo" href="./">
      <img src="logo.png" alt="로고">
      <b>국장 세력 판, 언제까지 당할래?</b><small>진짜와 가짜, 우리가 검증해 줄게.</small>
    </a>
    <a class="tlink" href="today">오늘의 급등주</a>
    <a class="cta" href="https://etforca.com/">다른 서비스도 보기 ${ARROW}</a>
  </header>`);

async function getJson(path){
  const r = await fetch(path, {cache: 'no-store'});
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}
const loadIndex = () => getJson('data/index.json');
const loadCase = id => getJson(`data/cases/${encodeURIComponent(id)}.json`);

function renderScore(cases){
  const s = scoreOf(cases);
  $('score').innerHTML = `밝혀진 거짓 <b>${s.confirmed}건</b> · ${s.rate === null ? rateText(s) : `<em>${rateText(s)}</em>`}`;
}
