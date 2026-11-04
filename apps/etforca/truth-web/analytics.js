/* PostHog 방문 분석 — 랜딩과 같은 웹 프로젝트(apps/etforca/app-landing/README.md '방문 분석'). 키는 공개 수집용(phc_)이다.
   기본은 랜딩처럼 쿠키 없이 센다(cookieless_mode 'always' — 고르기 전·거부도 방문은 집계된다).
   아래 막대에서 '허용'한 방문자만 다음 페이지부터 쿠키(1년 유지)로 센다. 선택은 localStorage 'truth-consent' 에 둔다.
   안내 문구는 각 페이지 끝 .cookie-note, 처리방침은 이 사이트의 privacy.html — 수집 항목을 바꾸면 둘 다 고친다.
   자동화 브라우저(게시 전 렌더 검사·CI)는 아무것도 보내지 않고 막대도 띄우지 않는다. */
if (!navigator.webdriver) {
  const KEY = 'truth-consent';
  // ponytail: 쿠키 동의를 묻기 시작하는 날. 이날 전에는 쿠키를 묻지 않는다 — 지난 뒤에는 이 줄과 조건을 지워도 된다.
  const ASK_FROM = '2026-10-28';
  const asking = new Date().toISOString().slice(0, 10) >= ASK_FROM;
  const read = () => { try { return localStorage.getItem(KEY); } catch { return null; } };
  const write = v => { try { v ? localStorage.setItem(KEY, v) : localStorage.removeItem(KEY); } catch { /* 저장 불가 — 매번 묻는다 */ } };
  const granted = asking && read() === 'yes';
  !function(t,e){var o,n,p,r;e.__SV||(window.posthog && window.posthog.__loaded)||(window.posthog=e,e._i=[],e.init=function(i,s,a){function g(t,e){var o=e.split(".");2==o.length&&(t=t[o[0]],e=o[1]),t[e]=function(){t.push([e].concat(Array.prototype.slice.call(arguments,0)))}}p||((p=t.createElement("script")).type="text/javascript",p.crossOrigin="anonymous",p.async=!0,p.src=s.api_host.replace(".i.posthog.com","-assets.i.posthog.com")+"/static/array.js",p.onerror=function(){p=null},(r=t.getElementsByTagName("script")[0]).parentNode.insertBefore(p,r));var u=e;for(void 0!==a?u=e[a]=[]:a="posthog",u.people=u.people||[],Object.defineProperty(u,"toString",{configurable:!0,enumerable:!0,writable:!0,value:function(t){var e="posthog";return"posthog"!==a&&(e+="."+a),t||(e+=" (stub)"),e}}),Object.defineProperty(u.people,"toString",{configurable:!0,enumerable:!0,writable:!0,value:function(){return u.toString(1)+".people (stub)"}}),o="init capture register register_once register_for_session unregister opt_out_capturing has_opted_out_capturing opt_in_capturing reset isFeatureEnabled getFeatureFlag getFeatureFlagPayload reloadFeatureFlags group identify setPersonProperties setPersonPropertiesForFlags resetPersonPropertiesForFlags setGroupPropertiesForFlags resetGroupPropertiesForFlags resetGroups onFeatureFlags addFeatureFlagsHandler onSessionId getSurveys getActiveMatchingSurveys renderSurvey canRenderSurvey getNextSurveyStep".split(" "),n=0;n<o.length;n++)g(u,o[n]);e._i.push([i,s,a])},e.__SV=1)}(document,window.posthog||[]);
  const PROJECT = 'phc_yiMfdC2Hi7iPBWHg7F9n3dBokvnXkjcsqFzN8Bs496R3';
  // 이 인스턴스의 저장 이름(ph_<프로젝트>_posthog…)과 SDK 의 쿠키 지원 확인용 쿠키만 지운다
  // — 같은 도메인의 포트폴리오 화면은 따로 동의받은 ph_orca_portfolio 를 쓴다
  const OWN = `ph_${PROJECT}_`;
  const ours = n => n.startsWith(OWN) || n.startsWith('__ph_cookie_support_');
  const dropCookies = () => {
    for (const name of document.cookie.split('; ').map(c => c.split('=')[0]).filter(ours)) {
      for (const domain of ['', `; domain=.${location.hostname.split('.').slice(-2).join('.')}`]) document.cookie = `${name}=; Max-Age=0; path=/${domain}`;
    }
    try { for (const k of Object.keys(localStorage)) if (k.startsWith(OWN)) localStorage.removeItem(k); } catch { /* 없음 */ }
  };
  // 거부로 바꾸면 지금 페이지의 저장도 끈다. SDK 가 아직 안 왔으면(스텁엔 set_config 가 없다) 로드될 때 끈다.
  const stopPersisting = () => {
    if (posthog.__loaded) posthog.set_config({ disable_persistence: true });
    dropCookies();
  };
  posthog.init(PROJECT, {
    api_host: 'https://us.i.posthog.com',
    // 허용: 쿠키에만 둔다(localStorage 는 만료가 없어 1년 약속을 못 지킨다). 그 밖: 쿠키 없는 집계
    ...(granted ? { persistence: 'cookie', cookie_expiration: 365 } : { cookieless_mode: 'always' }),
    defaults: '2026-05-30',
    person_profiles: 'identified_only',
    // 허용하지 않은 방문자: SDK 가 예전 저장분을 지우며 남기는 빈 쿠키까지 로드 뒤에 한 번 더 지운다
    loaded: () => { if (!granted || read() !== 'yes') stopPersisting(); },
  });
  const ask = () => {
    if (document.querySelector('.consent')) return;
    const bar = document.createElement('div');
    bar.className = 'consent glass';
    bar.setAttribute('role', 'dialog');
    bar.setAttribute('aria-label', '쿠키 사용 동의');
    bar.innerHTML = '<p>방문 통계에 쿠키를 써도 될까요? 허용하면 다시 방문할 때 같은 방문자로 셉니다(1년).</p>'
      + '<button type="button" data-choice="no">거부</button><button type="button" data-choice="yes">허용</button>';
    bar.addEventListener('click', e => {
      const choice = e.target.closest('[data-choice]')?.dataset.choice;
      if (!choice) return;
      write(choice);
      bar.remove();
      if (choice === 'no') {
        stopPersisting();
        // 쿠키 모드로 열린 페이지는 저장만 꺼서는 쿠키 없는 집계(서버 해시)로 바뀌지 않는다 — 다시 불러 바로 전환한다
        if (granted) location.reload();
      }
    });
    document.body.append(bar);
  };
  const ready = fn => document.body ? fn() : document.addEventListener('DOMContentLoaded', fn);
  if (!granted) dropCookies(); // 허용했다가 거부로 바꾼 뒤 남은 쿠키를 다음 페이지에서 지운다
  ready(() => {
    for (const b of document.querySelectorAll('[data-consent]')) b.hidden = !asking;
    if (asking && !read()) ask();
  });
  // 안내 문구의 '다시 고르기'
  document.addEventListener('click', e => {
    if (!asking || !e.target.closest('[data-consent]')) return;
    write(null);
    ask();
  });
  // 다른 탭에서 동의를 거두면 이 탭도 바로 쿠키 없는 집계로 바꾼다(쿠키 모드로 열린 탭만)
  addEventListener('storage', e => {
    if (e.key === KEY && granted && e.newValue !== 'yes') { stopPersisting(); location.reload(); }
  });
}
