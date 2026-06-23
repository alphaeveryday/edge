import { beforeAll, describe, expect, it } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

// loader → frame → widget 조립 경로를 한 번에 묶어 검증한다. 단위 테스트(widget.test.js,
// widget-loader.test.js)는 각 파일을 독립적으로만 보므로, frame이 본체를 안 불러오거나
// loader 출력 URL과 widget 입력 계약이 어긋나도 그쪽 테스트는 통과할 수 있다 — 그 간극을 막는다.

const widgetSource = fs.readFileSync(path.resolve(__dirname, '../widget.js'), 'utf8');
const loaderSource = fs.readFileSync(path.resolve(__dirname, '../widget-loader.js'), 'utf8');
const frameHtml = fs.readFileSync(path.resolve(__dirname, '../widget-frame.html'), 'utf8');

let widget;
let loader;

beforeAll(() => {
  window.__EDGE_WIDGET_TEST_MODE__ = true;
  window.eval(widgetSource);
  widget = window.__EDGE_WIDGET_INTERNALS__;

  window.__EDGE_WIDGET_LOADER_TEST_MODE__ = true;
  window.eval(loaderSource);
  loader = window.__EDGE_WIDGET_LOADER_INTERNALS__;
});

describe('loader ↔ frame ↔ widget 조립 계약', () => {
  it('widget-frame.html이 본체 widget.js를 로드한다', () => {
    // frame이 본체 스크립트 링크를 잃으면 iframe은 떠도 위젯이 부팅되지 않는다.
    expect(frameHtml).toMatch(/<script\s+src="\.\/widget\.js"><\/script>/);
  });

  it('loader가 만든 iframe URL fragment를 widget.readConfig가 동일하게 해석한다', () => {
    const url = loader.buildFrameUrl('https://cdn.example.com/widget/widget-loader.js', {
      key: 'pub_demo_1234',
      symbol: '005930',
    });
    const hash = new URL(url).hash; // #key=...&symbol=...
    expect(hash.startsWith('#')).toBe(true);

    // loader 출력(hash)을 그대로 본체 입력 계약(readConfig)에 통과시킨다.
    const config = widget.readConfig(hash);
    expect(config).toEqual({ embedKey: 'pub_demo_1234', symbol: '005930' });
  });

  it('frame URL은 key/symbol을 쿼리스트링이 아닌 fragment에만 싣는다 (로그 노출 방지)', () => {
    const url = new URL(
      loader.buildFrameUrl('https://cdn.example.com/widget/widget-loader.js', { key: 'k', symbol: 's' }),
    );

    expect(url.search).toBe('');
    expect(url.hash).toContain('key=k');
    expect(url.hash).toContain('symbol=s');
  });
});
