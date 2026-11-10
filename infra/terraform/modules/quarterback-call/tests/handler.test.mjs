// node --test infra/terraform/modules/quarterback-call/tests/handler.test.mjs — 람다 전역(awslambda)과 fetch·시크릿을 가짜로 바꿔 경로를 돈다.
import assert from 'node:assert/strict';
import { test } from 'node:test';

process.env.ALLOWED_ORIGINS = 'https://quarterback.etforca.com';
process.env.OPENAI_SECRET_ID = 'openai';
process.env.DEEPSEEK_SECRET_ID = 'deepseek';
process.env.ORIGIN_SECRET = 'cf-secret';

// 응답 스트림: 상태·머리와 쓴 글자를 모은다.
globalThis.awslambda = {
  HttpResponseStream: { from: (s, meta) => Object.assign(s, { meta }) },
  streamifyResponse: (fn) => fn,
};
const stream = () => ({ chunks: [], write(c) { this.chunks.push(c); }, end() { this.ended = true; } });

const mod = await import('../handler.mjs');

const event = (path, body, origin = 'https://quarterback.etforca.com', cf = 'cf-secret') => ({
  rawPath: path,
  requestContext: { http: { method: 'POST' } },
  headers: { origin, 'x-qb-origin': cf },
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

test('CloudFront 를 거치지 않은 호출은 경로·Origin 이 맞아도 거절한다 — 키 비용을 사이트 밖에서 못 쓰게', async () => {
  for (const cf of [null, '', 'guess']) {
    const s = stream();
    await mod.route(event('/api/call/token', { keywords: [] }, 'https://quarterback.etforca.com', cf), s);
    assert.equal(s.meta.statusCode, 403);
  }
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

test('휴대폰이 올린 통화 조각을 콘솔이 순서대로 이어 받는다 — 읽은 뒤의 것만', async () => {
  const rows = [];
  mod.setRoomStore({
    async append(room, events) {
      for (const e of events) rows.push({ room, seq: rows.length + 1, ...e });
    },
    async after(room, seq) {
      return rows.filter((r) => r.room === room && r.seq > seq).map(({ room: _r, ...e }) => e);
    },
  });
  const post = (body) => ({ ...event('/api/call/room/ROOM1234', body) });
  let s = stream();
  await mod.route(post({ events: [{ type: 'start' }, { type: 'delta', text: '로봇 왜' }] }), s);
  assert.equal(s.meta.statusCode, 200);
  s = stream();
  await mod.route(post({ events: [{ type: 'delta', text: ' 올랐어요?' }, { type: 'stop' }] }), s);
  // 콘솔의 GET 은 같은 사이트 요청이라 Origin 이 없다 — 그래도 읽혀야 한다.
  const get = (after) => ({ rawPath: '/api/call/room/ROOM1234', requestContext: { http: { method: 'GET' } }, headers: { 'x-qb-origin': 'cf-secret' }, queryStringParameters: { after: String(after) } });
  s = stream();
  await mod.route(get(0), s);
  const all = JSON.parse(s.chunks.join('')).events;
  assert.deepEqual(all.map((e) => e.type), ['start', 'delta', 'delta', 'stop']);
  assert.equal(all.filter((e) => e.text).map((e) => e.text).join(''), '로봇 왜 올랐어요?');
  s = stream();
  await mod.route(get(2), s);
  assert.deepEqual(JSON.parse(s.chunks.join('')).events.map((e) => e.seq), [3, 4]);
  // 다른 방은 보이지 않는다.
  s = stream();
  await mod.route({ ...get(0), rawPath: '/api/call/room/OTHER5678' }, s);
  assert.deepEqual(JSON.parse(s.chunks.join('')).events, []);
});

test('방 기록 입력 검사 — 잘못된 종류·긴 글·짧은 방 코드는 받지 않는다', async () => {
  assert.equal(typeof mod.parseRoomEvents({ events: [{ type: 'hack' }] }), 'string');
  assert.equal(typeof mod.parseRoomEvents({ events: [{ type: 'delta', text: 'x'.repeat(501) }] }), 'string');
  assert.equal(typeof mod.parseRoomEvents({ events: [] }), 'string');
  const s = stream();
  await mod.route(event('/api/call/room/abc', { events: [{ type: 'start' }] }), s);
  assert.equal(s.meta.statusCode, 404);
});

test('한 묶음이 같은 ms 에 들어와도 순번은 늘 커진다 — 종료가 뒤따르는 조각보다 앞서지 않게', () => {
  const seqs = Array.from({ length: 2500 }, () => mod.nextSeq());
  for (let i = 1; i < seqs.length; i++) assert.ok(seqs[i] > seqs[i - 1], `${i}: ${seqs[i - 1]} → ${seqs[i]}`);
});
