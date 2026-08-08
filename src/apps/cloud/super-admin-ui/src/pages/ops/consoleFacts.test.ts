/* 어댑터와 조회 상태 — 두 라운드 연속으로 결함이 여기 있었는데 단언이 0이었다 (ALPHA-738).
 *
 * 지키는 의도: ① 사건 식별자의 축(`sourceGroup`)을 어댑터가 버리면 딥링크가 남의 세션을 연다.
 * ② 값의 입도(날짜 축 집계)를 안 밝히면 규칙이 같은 사실을 벤더마다 복제한다.
 * ③ 조회 실패인데 캐시가 남은 상태를 "실림"으로 그리면 낡은 판정이 현재 사실로 읽힌다.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { axisOf, minuteFacts } from './consoleFacts.ts';
import type { MinuteStatus } from '../../domains/sources/types.ts';

const JOBS = (dead = 0) => ({ waiting: 0, claimed: 0, claimedExpired: 0, succeeded: 10, dead });

const session = (dataset: string, sourceGroup: string, dead = 0) =>
  ({
    sessionId: `${dataset}-${sourceGroup}`,
    dataset,
    sourceGroup,
    phase: 'ACTIVE',
    universeVersion: 'v1',
    expectedWindowCount: 390,
    processedThrough: null,
    contiguousCompleteThrough: null,
    heartbeatAt: null,
    leaseExpiresAt: null,
    leaseExpired: false,
    windows: { valid: 0, validEmpty: 0, incomplete: 0, invalid: 0, missing: 0, due: 0, claimed: 0, overdueNoEvidence: 2 },
    gaps: [],
    priceJobs: JOBS(dead),
  }) as unknown as MinuteStatus['sessions'][number];

const status = (sessions: MinuteStatus['sessions'], newsDead = 0): MinuteStatus =>
  ({ date: '2026-08-03', sessions, newsJobs: JOBS(newsDead) }) as unknown as MinuteStatus;

test('세션 identity 의 두 축을 다 옮긴다 — 벤더를 버리면 사건 키가 겹쳐 딥링크가 남의 세션을 연다', () => {
  const f = minuteFacts(
    status([session('news_minute', 'bigkinds'), session('news_minute', 'naver')]),
  );
  assert.deepEqual(
    f.sessions.map((s) => `${s.dataset}/${s.sourceGroup}`),
    ['news_minute/bigkinds', 'news_minute/naver'],
  );
  /* 빈 벤더를 통과시키지 않는지도 여기서 본다 — `''` 는 `targetId === ''` 가드를 우회하는
   * 합성 축이 되어 정상처럼 보이는 vid 를 만든다(가드는 합성 **전** 조각을 못 본다). */
  for (const s of f.sessions) assert.ok(s.sourceGroup, '벤더 축이 비었다');
});

test('뉴스 DEAD 는 날짜 축 집계라고 밝힌다 — 안 밝히면 규칙이 벤더마다 같은 사실을 복제한다', () => {
  const f = minuteFacts(
    status([session('news_minute', 'bigkinds'), session('news_minute', 'naver')], 3),
  );
  /* 두 세션 다 같은 값(3)을 받는다 — 그래서 **축을 밝히는 것**이 유일한 방어다 */
  assert.deepEqual(f.sessions.map((s) => s.deadJobs), [3, 3]);
  assert.deepEqual(f.sessions.map((s) => s.deadJobsByDate), [true, true]);
});

test('가격 DEAD 는 세션에 붙은 값이라 날짜 축 표기가 없다 (두 축을 뭉치면 방어가 사라진다)', () => {
  const f = minuteFacts(status([session('price_minute', 'KRX', 4)], 99));
  assert.equal(f.sessions[0].deadJobs, 4, '가격은 세션 job 을 읽어야 한다(날짜 집계 99 가 아니다)');
  assert.equal(f.sessions[0].deadJobsByDate, undefined);
});

test('조회 상태 — 데이터 유무와 조회 성공은 다른 축이다', () => {
  assert.equal(axisOf(false, false), 'pending');
  assert.equal(axisOf(false, true), 'error');
  assert.equal(axisOf(true, false), 'loaded');
  /* ⭐ react-query 는 에러가 나도 직전 데이터를 남긴다. `refetchInterval` 이 도는 화면에서
   * 5xx 가 나는 지배적 경로가 이것이고, 예전에는 이게 `loaded`("실시간 축 실림")로 그려져
   * **낡은 판정이 현재 사실처럼** 섰다. */
  assert.equal(axisOf(true, true), 'stale', '조회 실패인데 "실림"으로 그린다');
});
