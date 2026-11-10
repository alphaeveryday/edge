// node --test infra/terraform/modules/quarterback-call/tests/ — 람다 전역(awslambda)과 fetch·시크릿을 가짜로 바꿔 경로를 돈다.
import assert from 'node:assert/strict';
import { test } from 'node:test';

process.env.ALLOWED_ORIGINS = 'https://quarterback.etforca.com';
process.env.OPENAI_SECRET_ID = 'openai';
process.env.DEEPSEEK_SECRET_ID = 'deepseek';

// 응답 스트림: 상태·머리와 쓴 글자를 모은다.
globalThis.awslambda = {
  HttpResponseStream: { from: (s, meta) => Object.assign(s, { meta }) },
  streamifyResponse: (fn) => fn,
};
const stream = () => ({ chunks: [], write(c) { this.chunks.push(c); }, end() { this.ended = true; } });

const mod = await import('../handler.mjs');

const event = (path, body, origin = 'https://quarterback.etforca.com') => ({
  rawPath: path,
  requestContext: { http: { method: 'POST' } },
  headers: { origin },
  body: JSON.stringify(body),
});

test('입력 검사 — 키워드 개수·길이, 발화·리포트 필수', () => {
  assert.deepEqual(mod.parseTokenRequest({ keywords: ['로봇'] }), { keywords: ['로봇'] });
  assert.equal(typeof mod.parseTokenRequest({ keywords: Array(81).fill('a') }), 'string');
  assert.equal(typeof mod.parseTokenRequest({ keywords: ['x'.repeat(41)] }), 'string');
  assert.equal(typeof mod.parseSuggestRequest({ transcript: ' ', report: 'r' }), 'string');
  const long = mod.parseSuggestRequest({ transcript: 'a'.repeat(2000) + '끝', report: 'r' });
  assert.equal(long.transcript.length, 1500);
  assert.ok(long.transcript.endsWith('끝'), '최근 발화(끝)를 남긴다');
});

test('SSE 줄에서 답변 글자만 뽑는다', () => {
  assert.equal(mod.sseContent('data: {"choices":[{"delta":{"content":"오늘"}}]}'), '오늘');
  assert.equal(mod.sseContent('data: {"choices":[{"delta":{"reasoning_content":"생각"}}]}'), '');
  assert.equal(mod.sseContent('data: [DONE]'), '');
  assert.equal(mod.sseContent(': keep-alive'), '');
});

test('사이트 밖 Origin·없는 경로·GET 은 거절한다', async () => {
  let s = stream();
  await mod.route(event('/api/call/token', {}, 'https://evil.example'), s);
  assert.equal(s.meta.statusCode, 403);
  s = stream();
  await mod.route(event('/api/nope', {}), s);
  assert.equal(s.meta.statusCode, 404);
  s = stream();
  await mod.route({ ...event('/api/call/token', {}), requestContext: { http: { method: 'GET' } } }, s);
  assert.equal(s.meta.statusCode, 405);
});

test('답변 경로는 DeepSeek 스트림의 content 만 이어 붙여 흘린다', async (t) => {
  mod.secrets.set('deepseek', { DEEPSEEK_API_KEY: 'k' });
  const sse = [
    'data: {"choices":[{"delta":{"reasoning_content":"..."}}]}\n',
    'data: {"choices":[{"delta":{"content":"오늘 "}}]}\ndata: {"choi',
    'ces":[{"delta":{"content":"올랐어요."}}]}\n',
    'data: [DONE]\n',
  ];
  let sent;
  t.mock.method(globalThis, 'fetch', async (url, init) => {
    sent = { url, body: JSON.parse(init.body) };
    return new Response(new ReadableStream({ start(c) { sse.forEach((x) => c.enqueue(new TextEncoder().encode(x))); c.close(); } }));
  });
  const s = stream();
  await mod.route(event('/api/call/suggest', { transcript: '로봇 왜 올랐어요?', report: '로봇 +2.84%' }), s);
  assert.equal(s.meta.statusCode, 200);
  assert.equal(s.chunks.join(''), '오늘 올랐어요.');
  assert.ok(s.ended);
  assert.equal(sent.body.model, 'deepseek-flash');
  assert.equal(sent.body.reasoning_effort, 'low');
});
