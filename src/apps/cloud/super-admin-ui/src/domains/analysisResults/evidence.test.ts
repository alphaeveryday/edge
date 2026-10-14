/* 실행: node --test src/domains/analysisResults/evidence.test.ts */
import { strict as assert } from 'node:assert';
import test from 'node:test';
import { bullets, sources } from './evidence.ts';

test('문장은 sentence 글자 그대로 — JSON 원문이 아니다 (dev 에서 실제로 찍히던 모양)', () => {
  /* 상세 화면이 `{"sentence":…,"is_updated":false}` 를 그대로 찍던 결함(ALPHA-1337) */
  assert.deepEqual(bullets([{ sentence: '그래서 한 달은 위쪽, 되돌림 쪽으로 봐요.', is_updated: false }]), [
    { kind: 'sentence', text: '그래서 한 달은 위쪽, 되돌림 쪽으로 봐요.', updated: false },
  ]);
});

test('is_updated 는 참일 때만 갱신이다 — "true" 글자나 1 은 갱신이 아니다', () => {
  assert.deepEqual(
    bullets([
      { sentence: 'A', is_updated: true },
      { sentence: 'B', is_updated: 'true' },
      { sentence: 'C' },
    ]).map((b) => (b.kind === 'sentence' ? b.updated : null)),
    [true, false, false],
  );
});

test('출처는 제목 글자의 링크다', () => {
  assert.deepEqual(sources([{ url: 'https://news.bizwatch.co.kr/a', title: '삼전·닉스, 또 최대 실적' }]), [
    { kind: 'link', text: '삼전·닉스, 또 최대 실적', href: 'https://news.bizwatch.co.kr/a' },
  ]);
});

test('제목 없는 출처는 url 을 글자로 쓴다', () => {
  for (const s of [{ url: 'http://x.kr/1' }, { url: 'http://x.kr/1', title: '' }, { url: 'http://x.kr/1', title: null }]) {
    assert.deepEqual(sources([s]), [{ kind: 'link', text: 'http://x.kr/1', href: 'http://x.kr/1' }]);
  }
});

test('http·https 가 아닌 url 은 링크로 만들지 않는다 — 글자로만', () => {
  for (const url of ['javascript:alert(1)', 'ftp://x.kr/f', 'data:text/html,hi', '/relative/path', 'news.example']) {
    const [s] = sources([{ url, title: '제목' }]);
    assert.deepEqual(s, { kind: 'text', text: '제목' }, url);
  }
});

test('예상한 키가 없는 값은 원문으로 보인다 — 화면이 깨지지 않는다', () => {
  assert.deepEqual(bullets([{ text: '다른 키' }, 3, null, ['a']]), [
    { kind: 'raw', text: '{"text":"다른 키"}' },
    { kind: 'raw', text: '3' },
    { kind: 'raw', text: 'null' },
    { kind: 'raw', text: '["a"]' },
  ]);
  assert.deepEqual(sources([{ title: 'url 없음' }, 'https://bare.string']), [
    { kind: 'raw', text: '{"title":"url 없음"}' },
    { kind: 'raw', text: 'https://bare.string' },
  ]);
});

test('배열이 아니거나 비어 있으면 그대로 따라간다 — 없는 것을 지어내지 않는다', () => {
  assert.deepEqual(bullets(null), []);
  assert.deepEqual(sources(undefined), []);
  assert.deepEqual(sources([]), []);
  assert.deepEqual(bullets({ sentence: '한 개', is_updated: false }), [{ kind: 'sentence', text: '한 개', updated: false }]);
});
