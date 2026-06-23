import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

const loaderSource = fs.readFileSync(path.resolve(__dirname, '../widget-loader.js'), 'utf8');
let internals;

function bootstrapInternals() {
  if (!internals) {
    window.__EDGE_WIDGET_LOADER_TEST_MODE__ = true;
    window.eval(loaderSource);
    internals = window.__EDGE_WIDGET_LOADER_INTERNALS__;
  }
}

const LOADER_SRC = 'https://cdn.example.com/widget/widget-loader.js';

function buildScript(attrs = {}) {
  const script = document.createElement('script');
  script.src = LOADER_SRC;
  if (attrs.key !== null) script.setAttribute('data-embed-key', attrs.key ?? 'pub_demo_1234');
  if (attrs.symbol !== null) script.setAttribute('data-symbol', attrs.symbol ?? '005930');
  return script;
}

describe('widget-loader', () => {
  beforeAll(() => {
    bootstrapInternals();
  });

  beforeEach(() => {
    document.body.innerHTML = '';
  });

  it('readLoaderConfig가 data-embed-key/data-symbol를 읽고 trim한다', () => {
    const config = internals.readLoaderConfig(buildScript({ symbol: '  005930  ' }));

    expect(config).toEqual({ key: 'pub_demo_1234', symbol: '005930' });
  });

  // key·symbol은 쿼리스트링이 아니라 fragment(#…)로 실어 서버/CDN/Referer 로그 노출을 막는다.
  const fragParams = (urlStr) => new URLSearchParams(new URL(urlStr).hash.replace(/^#/, ''));

  it('buildFrameUrl이 loader src 기준으로 widget-frame.html#key=&symbol= 를 만든다', () => {
    const urlStr = internals.buildFrameUrl(LOADER_SRC, { key: 'pub_demo_1234', symbol: '005930' });
    const url = new URL(urlStr);

    expect(url.origin).toBe('https://cdn.example.com');
    expect(url.pathname).toBe('/widget/widget-frame.html');
    expect(url.search).toBe(''); // 쿼리스트링에는 절대 싣지 않는다
    const params = fragParams(urlStr);
    expect(params.get('key')).toBe('pub_demo_1234');
    expect(params.get('symbol')).toBe('005930');
  });

  it('buildFrameUrl은 빈 값 파라미터를 fragment에 넣지 않는다', () => {
    const params = fragParams(internals.buildFrameUrl(LOADER_SRC, { key: '', symbol: '005930' }));

    expect(params.has('key')).toBe(false);
    expect(params.get('symbol')).toBe('005930');
  });

  it('buildFrameUrl은 계약(key·symbol) 외 필드를 fragment에 흘리지 않는다', () => {
    const params = fragParams(internals.buildFrameUrl(LOADER_SRC, { key: 'pub_demo_1234', symbol: '005930', mock: 'error' }));

    // fragment에는 정확히 key·symbol만 실려야 한다. buildFrameUrl이 임의 config 필드를
    // 전달하기 시작하면(allowlist 누출) 키 집합이 달라져 이 단언이 실패한다.
    expect([...params.keys()].sort()).toEqual(['key', 'symbol']);
    expect(params.get('key')).toBe('pub_demo_1234');
    expect(params.get('symbol')).toBe('005930');
  });

  it('createIframe이 100% 폭·border 0·초기 높이를 가진 iframe을 만든다', () => {
    const iframe = internals.createIframe('https://cdn.example.com/widget/widget-frame.html?key=k&symbol=s');

    expect(iframe.tagName).toBe('IFRAME');
    expect(iframe.getAttribute('src')).toContain('widget-frame.html');
    expect(iframe.style.width).toBe('100%');
    expect(iframe.style.border).toBe('0px');
    expect(iframe.style.height).toBe(internals.MIN_HEIGHT + 'px');
    expect(iframe.getAttribute('scrolling')).toBe('no');
  });

  it('insertIframe이 script 태그 바로 뒤에 iframe을 삽입한다', () => {
    const script = buildScript();
    document.body.appendChild(script);
    const iframe = internals.createIframe('about:blank');

    internals.insertIframe(script, iframe);

    expect(script.nextSibling).toBe(iframe);
  });

  it('initLoader가 script 뒤에 widget-frame iframe을 삽입한다', () => {
    const script = buildScript();
    document.body.appendChild(script);

    const iframe = internals.initLoader(script);

    expect(iframe.tagName).toBe('IFRAME');
    expect(script.nextSibling).toBe(iframe);
    const url = new URL(iframe.src);
    expect(url.pathname.endsWith('widget-frame.html')).toBe(true);
    expect(url.search).toBe('');
    const params = fragParams(iframe.src);
    expect(params.get('key')).toBe('pub_demo_1234');
    expect(params.get('symbol')).toBe('005930');
  });

  it('기준 script를 못 찾으면 조용히 끝내지 않고 콘솔 에러를 남긴다', () => {
    // currentScript가 null인 주입 경로(async/defer, 태그매니저)에서 위젯이 흔적 없이
    // 사라지면 고객사가 진단할 수 없다 — null 반환과 함께 콘솔 에러로 실패를 알려야 한다.
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    try {
      expect(internals.initLoader()).toBe(null); // override 없음 + currentScript 없음
      expect(spy).toHaveBeenCalledTimes(1);
    } finally {
      spy.mockRestore();
    }
  });

  describe('applyHeightMessage (postMessage 높이 동기화)', () => {
    function fakeIframe() {
      const contentWindow = {};
      return { iframe: { contentWindow, style: {} }, contentWindow };
    }

    it('이 iframe이 보낸 유효한 height 메시지로 iframe 높이를 갱신한다', () => {
      const { iframe, contentWindow } = fakeIframe();

      const result = internals.applyHeightMessage(iframe, {
        source: contentWindow,
        data: { type: internals.HEIGHT_MESSAGE_TYPE, height: 480 },
      });

      expect(result).toBe(480);
      expect(iframe.style.height).toBe('480px');
    });

    it('MIN_HEIGHT보다 작은 높이는 MIN_HEIGHT로 올린다', () => {
      const { iframe, contentWindow } = fakeIframe();

      internals.applyHeightMessage(iframe, {
        source: contentWindow,
        data: { type: internals.HEIGHT_MESSAGE_TYPE, height: 10 },
      });

      expect(iframe.style.height).toBe(internals.MIN_HEIGHT + 'px');
    });

    it('다른 source(다른 프레임)의 메시지는 무시한다', () => {
      const { iframe } = fakeIframe();

      const result = internals.applyHeightMessage(iframe, {
        source: {},
        data: { type: internals.HEIGHT_MESSAGE_TYPE, height: 480 },
      });

      expect(result).toBeNull();
      expect(iframe.style.height).toBeUndefined();
    });

    it('type이 다른 메시지는 무시한다', () => {
      const { iframe, contentWindow } = fakeIframe();

      const result = internals.applyHeightMessage(iframe, {
        source: contentWindow,
        data: { type: 'something-else', height: 480 },
      });

      expect(result).toBeNull();
    });

    it('숫자가 아니거나 0 이하인 height는 무시한다', () => {
      const { iframe, contentWindow } = fakeIframe();

      expect(internals.applyHeightMessage(iframe, {
        source: contentWindow,
        data: { type: internals.HEIGHT_MESSAGE_TYPE, height: 'tall' },
      })).toBeNull();
      expect(internals.applyHeightMessage(iframe, {
        source: contentWindow,
        data: { type: internals.HEIGHT_MESSAGE_TYPE, height: 0 },
      })).toBeNull();
    });
  });

  it('테스트 모드에서만 loader internals를 노출한다', () => {
    delete window.__EDGE_WIDGET_LOADER_INTERNALS__;
    window.__EDGE_WIDGET_LOADER_TEST_MODE__ = false;

    window.eval(loaderSource);

    expect(window.__EDGE_WIDGET_LOADER_INTERNALS__).toBeUndefined();

    window.__EDGE_WIDGET_LOADER_TEST_MODE__ = true;
    window.__EDGE_WIDGET_LOADER_INTERNALS__ = internals;
  });
});
