/* 어댑터와 조회 상태 — 두 라운드 연속으로 결함이 여기 있었는데 단언이 0이었다 (ALPHA-738).
 *
 * 지키는 의도: ① 사건 식별자의 축(`sourceGroup`)을 어댑터가 버리면 딥링크가 남의 세션을 연다.
 * ② 값의 입도(날짜 축 집계)를 안 밝히면 규칙이 같은 사실을 벤더마다 복제한다.
 * ③ 조회 실패인데 캐시가 남은 상태를 "실림"으로 그리면 낡은 판정이 현재 사실로 읽힌다.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { awsObservation, axisOf, minuteFacts } from './consoleFacts.ts';
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
  /* 어휘 정본은 `data_pipeline/minute/states.py` 의 `SOURCE_GROUPS_BY_DATASET` 다:
   * `price_minute` = {toss, kis}(교체 운용) · `news_minute` = {bigkinds} 하나. 그래서 **같은
   * 날짜에 세션이 둘인 실제 상태는 가격 레인 교체일**이다 — 픽스처도 그걸 쓴다(어휘 밖 값을
   * 쓰면 초록이 프로덕션 상태를 증명하지 못한다). */
  const f = minuteFacts(status([session('price_minute', 'kis'), session('price_minute', 'toss')]));
  assert.deepEqual(
    f.sessions.map((s) => `${s.dataset}/${s.sourceGroup}`),
    ['price_minute/kis', 'price_minute/toss'],
  );
});

test('빈 벤더를 메우지 않는다 — 어댑터가 채우면 하류의 조각 가드가 영원히 안 뜬다', () => {
  /* 조각 가드는 여기가 아니라 `rules.ts` 의 `sessionTarget` 에 있다(합성 전에 접는다).
   * 그 가드가 뜨려면 **빈 값이 그대로 도착해야** 한다 — 어댑터가 `?? '알 수 없음'` 같은 걸로
   * 메우면 `price_minute/알 수 없음` 이라는 정상처럼 보이는 사건 키가 서고, 하류 가드는
   * 죽은 분기가 된다. 여기서 막을 수 있는 것은 그 '메움'뿐이라 그것만 단언한다. */
  const f = minuteFacts(status([session('price_minute', '')]));
  assert.equal(f.sessions[0].sourceGroup, '', '빈 벤더를 값으로 메웠다 — 하류 가드가 안 뜬다');
});

test('어휘 밖 데이터셋의 job 원장은 0이 아니라 모름이다 — 부재를 "봤고 괜찮다"로 접지 않는다', () => {
  /* 세 번째 실시간 데이터셋(`inav_minute`)이 붙는 날의 모양이다. `datasetKind` 가 'other' 를
   * 내는데 어댑터가 `priceJobs` 로 접으면, 응답에 그 원장 행이 없어 **0**이 되고 R19 가
   * `평가됨 · 조건에 걸린 것 없음` 을 낸다 — 원장 부재가 정상으로 그려진다. */
  const f = minuteFacts(status([session('inav_minute', 'kis')]));
  assert.equal(f.sessions[0].deadJobs, null, '모르는 원장을 0으로 채웠다');
  /* 날짜 축 맵에도 안 들어간다 — 어느 원장을 읽어야 할지 모르는 데이터셋이다 */
  assert.equal('inav_minute' in f.deadJobsByDataset, false);
});

test('뉴스 DEAD 는 날짜 축 집계라고 밝힌다 — 안 밝히면 규칙이 벤더마다 같은 사실을 복제한다', () => {
  /* 오늘 뉴스 벤더는 `bigkinds` 하나다(어휘 정본). 이 단언이 지키는 것은 **벤더가 늘 때**
   * 날짜 축 집계가 세션마다 복제되지 않는다는 불변식이다 — 그때 규칙이 조용히 두 배로 센다. */
  const f = minuteFacts(
    status([session('news_minute', 'bigkinds'), session('news_minute', 'future_vendor')], 3),
  );
  /* 값이 세션에 안 실린다 — 세션 축으로는 **모름**이다(그 원장이 세션에 안 붙어 있다) */
  assert.deepEqual(f.sessions.map((s) => s.deadJobs), [null, null]);
  /* 값은 데이터셋 하나당 **한 자리**에 선다. 세션에 실려 있던 동안은 벤더 수만큼 복제할
   * 여지가 구조적으로 남아 있었다(둘 다 3을 들고 있었다). */
  assert.deepEqual(f.deadJobsByDataset, { news_minute: 3 });
});

test('가격 DEAD 는 세션에 붙은 값이다 (두 축을 뭉치면 방어가 사라진다)', () => {
  const f = minuteFacts(status([session('price_minute', 'kis', 4)], 99));
  assert.equal(f.sessions[0].deadJobs, 4, '가격은 세션 job 을 읽어야 한다(날짜 집계 99 가 아니다)');
  assert.equal('price_minute' in f.deadJobsByDataset, false, '가격을 날짜 축에 실었다');
});

test('🔴 뉴스 세션이 없어도 그날 DEAD 는 실린다 — 세션 순회로 읽으면 하필 그날 조용해진다', () => {
  /* `news_extraction_job` 에는 `session_id` 도 `session_date` 도 없다(가격 job 은 `session_id` 를
   * 가진다). `newsJobs` 는 `created_at` 하루 창 집계라 **세션과 다른 컬럼으로 잘린다**.
   * 세션이 없는 날은 실제로 있다: 아침 planner 전(자정~계획 사이의 재시도) · 비거래일 ·
   * **뉴스 계획만 실패한 날**(파이프라인이 의도적으로 만드는 경로 — 가격은 세우고 news-worker 는
   * 안 올린다). 하필 그날이 R19 가 가장 시끄러워야 할 날이다. */
  const noSession = minuteFacts(status([], 3));
  assert.deepEqual(noSession.sessions, []);
  assert.deepEqual(noSession.deadJobsByDataset, { news_minute: 3 }, '세션이 없다고 유실을 버렸다');

  /* 가격 세션만 있는 날도 같다 — 뉴스 계획만 실패한 날의 실제 모양이다 */
  const priceOnly = minuteFacts(status([session('price_minute', 'kis', 0)], 3));
  assert.deepEqual(priceOnly.deadJobsByDataset, { news_minute: 3 });
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

test('AWS 관측 부재는 두 형상이다 — 미배선과 조회 실패를 한 칸에 그리지 않는다', () => {
  /* 포매터(`kst`)에 그냥 넘기면 둘 다 `—`(집계 없음)가 된다. 그러면 제어면 장애(AccessDenied)가
   * "아직 계측이 없구나"로 읽히고, 운영자는 고칠 수 있는 것을 못 고친다. */
  assert.equal(awsObservation({ db: 'd', today: 't' }), 'uninstrumented', '키 부재는 미배선이다');
  assert.equal(
    awsObservation({ db: 'd', today: 't', aws: undefined }),
    'uninstrumented',
    '명시한 undefined 도 미배선이다 — `in` 으로 가르면 여기서 조회 실패로 뒤집힌다',
  );
  assert.equal(awsObservation({ db: 'd', today: 't', aws: null }), 'blind', 'null 은 조회 실패다');
  assert.deepEqual(awsObservation({ db: 'd', today: 't', aws: 'x' }), { at: 'x' });
});
