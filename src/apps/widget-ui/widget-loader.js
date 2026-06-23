(() => {
  'use strict';

  // 고객사 페이지에 들어가는 얇은 로더. 단일 <script>가 data-* 입력을 읽어 위젯 본체를
  // iframe(widget-frame.html)으로 띄우고, #key=&symbol=(fragment)로 입력을 넘긴다. 실제 위젯(widget.js)은
  // iframe 안에서 돌아 CSS가 고객사 페이지와 격리된다. 본체가 postMessage로 높이를 알려오면
  // iframe 높이를 맞춰 모바일에서도 스크롤바가 생기지 않게 한다.

  const HEIGHT_MESSAGE_TYPE = 'edge-widget:height';
  const FRAME_FILE = 'widget-frame.html';
  const IFRAME_TITLE = 'EDGE 위젯';
  const MIN_HEIGHT = 120; // skeleton/초기 높이(px). 본체 높이를 받기 전까지 유지한다.

  function initLoader(scriptOverride) {
    const script = scriptOverride || document.currentScript;
    if (!script) {
      // currentScript가 null인 경우(async/defer 주입, 태그매니저 등) iframe을 만들 기준 script가 없다.
      // 조용히 사라지면 고객사가 진단할 수 없으므로 최소한 콘솔에 남긴다.
      console.error('[EDGE Widget] 로더 script 태그를 찾을 수 없어 위젯을 띄울 수 없습니다.');
      return null;
    }

    const config = readLoaderConfig(script);
    const iframe = createIframe(buildFrameUrl(script.src, config));
    insertIframe(script, iframe);
    listenForHeight(iframe);
    return iframe;
  }

  function readLoaderConfig(script) {
    return {
      key: normalize(script.getAttribute('data-embed-key')),
      symbol: normalize(script.getAttribute('data-symbol')),
    };
  }

  // 로더 script의 src 기준으로 widget-frame.html 경로를 풀어, 같은 출처에서 본체를 로드한다.
  // key·symbol은 쿼리스트링이 아니라 URL fragment(#…)로 싣는다 — fragment는 HTTP 요청 라인과
  // Referer 헤더에 포함되지 않아, 종목/키가 정적 서버·CDN·프록시 로그에 남지 않는다.
  function buildFrameUrl(scriptSrc, config) {
    const url = new URL(FRAME_FILE, scriptSrc);
    const params = new URLSearchParams();
    if (config.key) params.set('key', config.key);
    if (config.symbol) params.set('symbol', config.symbol);
    url.hash = params.toString();
    return url.toString();
  }

  function createIframe(src) {
    const iframe = document.createElement('iframe');
    iframe.className = 'edge-widget-frame';
    iframe.src = src;
    iframe.title = IFRAME_TITLE;
    iframe.setAttribute('scrolling', 'no');
    // loading="lazy"는 쓰지 않는다 — AI 탭이 처음 숨겨져 있어도 즉시 로드해, 탭 진입 시 첫 요청
    // 지연 없이 바로 분석이 보이게 한다(위젯이 작아 lazy 이득보다 첫 클릭 지연 리스크가 크다).
    iframe.style.display = 'block';
    iframe.style.width = '100%';
    iframe.style.border = '0';
    iframe.style.height = MIN_HEIGHT + 'px';
    return iframe;
  }

  function insertIframe(script, iframe) {
    if (script.parentNode) {
      script.parentNode.insertBefore(iframe, script.nextSibling);
    } else {
      document.body.appendChild(iframe);
    }
  }

  function listenForHeight(iframe) {
    window.addEventListener('message', (event) => applyHeightMessage(iframe, event));
  }

  // 이 iframe이 보낸 height 메시지만 수용한다. event.source 동일성 확인이 현재 방어선이고,
  // origin 엄격검증(서버 allowed_origins/frame-ancestors)은 서버 트랙에서 결합한다.
  function applyHeightMessage(iframe, event) {
    if (!event || event.source !== iframe.contentWindow) {
      return null;
    }
    const data = event.data;
    if (!data || data.type !== HEIGHT_MESSAGE_TYPE) {
      return null;
    }
    const height = Number(data.height);
    if (!Number.isFinite(height) || height <= 0) {
      return null;
    }
    const next = Math.max(Math.ceil(height), MIN_HEIGHT);
    iframe.style.height = next + 'px';
    return next;
  }

  function normalize(value) {
    return String(value || '').trim();
  }

  if (window.__EDGE_WIDGET_LOADER_TEST_MODE__) {
    window.__EDGE_WIDGET_LOADER_INTERNALS__ = {
      initLoader,
      readLoaderConfig,
      buildFrameUrl,
      createIframe,
      insertIframe,
      listenForHeight,
      applyHeightMessage,
      normalize,
      HEIGHT_MESSAGE_TYPE,
      MIN_HEIGHT,
    };
  } else {
    initLoader();
  }
})();
