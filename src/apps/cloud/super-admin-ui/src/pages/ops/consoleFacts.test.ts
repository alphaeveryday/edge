/* 어댑터와 조회 상태 — 두 라운드 연속으로 결함이 여기 있었는데 단언이 0이었다 (ALPHA-738).
 *
 * 지키는 의도: ① 사건 식별자의 축(`sourceGroup`)을 어댑터가 버리면 딥링크가 남의 세션을 연다.
 * ② 값의 입도(날짜 축 집계)를 안 밝히면 규칙이 같은 사실을 벤더마다 복제한다.
 * ③ 조회 실패인데 캐시가 남은 상태를 "실림"으로 그리면 낡은 판정이 현재 사실로 읽힌다.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  awsObservation,
  axisOf,
  BOUNDARY_FIELDS,
  CHAIN_FEED_FIELDS,
  CHAIN_STAGE_FIELDS,
  DATASET_FIELDS,
  factsAxis,
  META_FIELDS,
  minuteFacts,
  OUTPUT_FIELDS,
  parseFacts,
  RUN_FIELDS,
  TASK_FIELDS,
} from './consoleFacts.ts';
import type { ConsoleFactsDto } from '../../domains/console/types.ts';
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

/* ── 응답 검증 경계 ──────────────────────────────────────────────────────────
 * 이 경계가 존재하는 이유는 "규칙마다 값 가드를 다는 방식이 끝나지 않아서"다. 그러니 **거부
 * 조건 하나하나에 단언이 있어야** 한다 — 없으면 다음 라운드가 그 조건을 지워도 아무도 모른다.
 * 계약이 "검증 경계가 답할 몫"으로 열거한 목록이 곧 이 테스트의 목록이다. */

/* ⚠️ **모든 자리에 다른 값을 둔다.** 두 필드가 같은 값이면 그 둘을 맞바꾸는 매핑 실수가
 * deepEqual 로도 안 잡힌다 — 실제로 `completenessExpected == received == 33` 과
 * `deadline == ledgerUpdated == null` 이던 동안 변이 2종이 통과했다. */
const WIRE = (): ConsoleFactsDto => ({
  runs: [{
    id: 'etf-daily:2026-08-03T15:40', lane: 'etf-daily', tradingDate: '2026-08-03',
    ledgerStatus: 'RUNNING', ledgerUpdated: '2026-08-03T16:10:36+09:00',
    deadline: '2026-08-03T21:40:00+09:00',
  }],
  tasks: [{
    taskKey: 'T', runId: 'etf-daily:2026-08-03T15:40', pipelineType: 'etf-daily',
    tradingDate: '2026-08-03', stage: 'raw', dataset: 'etf_holdings', required: true,
    planStatus: 'DUE', taskOutcome: 'FULFILLED', dataStatus: 'VALID',
    recordsOut: 906, failedRecords: 0,
    completenessExpected: 33, completenessReceived: 30, completenessMissing: 3, attempts: 2,
  }],
  datasets: [{
    id: 'etf_holdings', contract: true, expectedAsOf: '2026-08-03', actualAsOf: null,
    collectedAt: '2026-08-03T15:41:58+09:00', unverifiable: 'ACTUAL_AS_OF_UNVERIFIED',
  }],
  outputs: [{ id: 'o.pub', label: '게시 ETF', today: 16, base: 32, unit: '종' }],
  boundary: { publishedWithoutDelivery: 0, deliveryNowNonpublished: 1, deliveryRows: 114 },
  /* 네 수를 전부 다르게 둔다 — 갈래(batch↔intraday)나 피드↔단계를 맞바꾸는 변이는 같은 값
   * 위에서는 안 보인다. 단계도 **둘** 둔다: 하나면 목록 순서를 뒤집는 변이가 no-op 이 된다. */
  chain: {
    feeds: [
      { id: 'feed.batch', label: '배치 트리거', v: 20, unit: 'ETF', src: 'price_movement_trigger' },
      { id: 'feed.intraday', label: '장중 트리거', v: 65, unit: '건', src: 'minute_price_trigger' },
    ],
    stages: [
      { id: 'c.obs', label: '관측', batch: 18, intraday: 3, src: 'etf_contribution_observation' },
      { id: 'c.route', label: '라우트', batch: 17, intraday: 1, src: 'explanation_route' },
    ],
  },
  meta: { db: '2026-08-03T16:20:00+09:00', today: '2026-08-03' },
});

/** 한 자리만 망가뜨린 응답 — 나머지는 정상이라 거부가 그 자리 때문임이 분명하다. */
const broken = (mutate: (w: ConsoleFactsDto) => void) => {
  const w = WIRE();
  mutate(w);
  return parseFacts(w);
};

test('어댑터는 이름만 바꾼다 — 전 필드를 값 그대로 옮긴다', () => {
  /* 🔴 **일부 필드만 단언하면 나머지 매핑이 무방비다.** `plan_status` 매핑을 지워도 그 필드가
   * 옵셔널이라 tsc 는 통과하고, 그러면 SKIPPED 작업이 `undefined !== 'SKIPPED'` 로 R05 에 들어가
   * **거짓 P0** 가 난다(리뷰가 잡았다). 그래서 **전체를 deepEqual** 한다 — 한 필드가 빠지거나
   * 엉뚱한 자리로 가면 여기서 걸린다. */
  const r = parseFacts(WIRE());
  assert.equal(r.ok, true);
  if (!r.ok) return;
  assert.deepEqual(r.facts, {
    runs: [{
      id: 'etf-daily:2026-08-03T15:40',
      lane: 'etf-daily',
      trading_date: '2026-08-03',
      ledger_status: 'RUNNING',
      ledger_updated: '2026-08-03T16:10:36+09:00',
      deadline: '2026-08-03T21:40:00+09:00',
    }],
    tasks: [{
      task_key: 'T',
      /* 사건을 런에 매다는 축 — 어긋나면 인과 간선이 조용히 안 걸린다 */
      run_id: 'etf-daily:2026-08-03T15:40',
      pipeline_type: 'etf-daily',
      trading_date: '2026-08-03',
      stage: 'raw',
      dataset: 'etf_holdings',
      required: true,
      plan_status: 'DUE',
      task_outcome: 'FULFILLED',
      data_status: 'VALID',
      records_out: 906,
      failed_records: 0,
      completeness_expected: 33,
      completeness_received: 30,
      completeness_missing: 3,
      attempts: 2,
    }],
    datasets: [{
      id: 'etf_holdings',
      contract: true,
      expected_as_of: '2026-08-03',
      actual_as_of: null,
      collected_at: '2026-08-03T15:41:58+09:00',
      unverifiable: 'ACTUAL_AS_OF_UNVERIFIED',
    }],
    outputs: [{ id: 'o.pub', label: '게시 ETF', today: 16, base: 32, unit: '종' }],
    boundary: {
      published_without_delivery: 0,
      delivery_now_nonpublished: 1,
      delivery_rows: 114,
    },
    chain: {
      feeds: [
        { id: 'feed.batch', label: '배치 트리거', v: 20, unit: 'ETF', src: 'price_movement_trigger' },
        { id: 'feed.intraday', label: '장중 트리거', v: 65, unit: '건', src: 'minute_price_trigger' },
      ],
      stages: [
        { id: 'c.obs', label: '관측', batch: 18, intraday: 3, src: 'etf_contribution_observation' },
        { id: 'c.route', label: '라우트', batch: 17, intraday: 1, src: 'explanation_route' },
      ],
    },
    meta: { db: '2026-08-03T16:20:00+09:00', today: '2026-08-03' },
  });
});

test('🔴 체인의 순서를 어댑터가 바꾸지 않는다 — 목록 순서가 곧 흐름이다', () => {
  /* `deepEqual` 은 순서를 보지만 픽스처가 이미 "옳은" 순서라 정렬을 **넣는** 변이만 잡는다.
   * 여기서는 **서버가 준 순서가 무엇이든 그대로**임을 잰다 — 어댑터가 id 로 다시 찾거나
   * 정렬하면, 원장에 단계 간 선후가 없어 복원할 방법이 없는 순서가 조용히 뒤집힌다.
   * 뒤집힌 순서 위에서 R10 은 감소를 증가로 읽어 **P0 손실을 통째로 놓친다**. */
  const w = WIRE();
  w.chain.stages.reverse();
  const r = parseFacts(w);
  assert.equal(r.ok, true);
  if (!r.ok) return;
  assert.deepEqual(r.facts.chain?.stages.map((s) => s.id), ['c.route', 'c.obs']);
  assert.deepEqual(r.facts.chain?.stages.map((s) => s.batch), [17, 18]);
});

test('서버가 안 보낸 축을 어댑터가 만들어 내지 않는다', () => {
  /* 🔴 `queues: []`·`runbook: {}` 로 메우면 계측 없음이 실측 0 으로 위조되고, 규칙이 `못 돎`
   * 대신 `평가됨 · 위반 0`("봤고 괜찮다")을 세운다 — 이 트랙이 없애려는 칸 혼동이다.
   * 남은 셋은 전부 **AWS 제어면**이라 ALPHA-979 조각 2·3 이 닫는다. */
  const r = parseFacts(WIRE());
  assert.equal(r.ok, true);
  if (!r.ok) return;
  assert.equal(r.facts.runbook, undefined, 'runbook 축을 만들어 냈다');
  assert.equal(r.facts.meta.aws, undefined, 'AWS 관측 시각을 만들어 냈다');
  assert.equal(r.facts.queues, undefined, 'queues 축을 만들어 냈다');
  /* 체인은 이제 **온다** — 이 단언이 없으면 위 셋을 지키느라 축을 통째로 떨구는 회귀가 초록이다 */
  assert.notEqual(r.facts.chain, undefined, 'chain 축을 떨궜다');
});

test('런 행이 없는 슬롯의 계획 표시는 실린 것만 옮긴다', () => {
  /* `planned`·`noRunRow` 는 서버가 그 슬롯에만 싣는다(필드 단위 NON_NULL). 실재 런에 `false` 를
   * 만들어 붙이면 "계획된 적 없는 런"이라는, 원장이 하지 않은 단정이 화면에 선다. */
  const w = WIRE();
  w.runs.push({ ...w.runs[0], id: 'etf-daily:2026-08-03T21:40', ledgerStatus: null,
    planned: true, noRunRow: true } as never);
  const r = parseFacts(w);
  assert.equal(r.ok, true);
  if (!r.ok) return;
  assert.ok(!('planned' in r.facts.runs[0]), '실재 런에 계획 표시를 만들어 붙였다');
  assert.equal(r.facts.runs[1].planned, true);
  assert.equal(r.facts.runs[1].no_run_row, true);
});

test('거부 — 컬렉션 원소가 객체가 아니면 응답 전체를 버린다', () => {
  /* 규칙 층이 의도적으로 안 막은 자리다. 여기서 통과시키면 `outputs[null].today` 가 규칙 안에서
   * 터지고, 그건 그 규칙이 아니라 **평가 전체**를 죽인다. */
  for (const [name, mutate] of [
    ['outputs: [null]', (w: ConsoleFactsDto) => { (w.outputs as unknown[])[0] = null; }],
    ['runs: [스칼라]', (w: ConsoleFactsDto) => { (w.runs as unknown[])[0] = 1; }],
    ['tasks: [배열]', (w: ConsoleFactsDto) => { (w.tasks as unknown[])[0] = []; }],
    ['datasets 가 객체', (w: ConsoleFactsDto) => { (w as { datasets: unknown }).datasets = {}; }],
  ] as const) {
    assert.equal(broken(mutate).ok, false, `${name} 을 통과시켰다`);
  }
});

test('거부 — 세는 값이 수가 아니거나 음수면 버린다', () => {
  /* `NaN` 은 `!= null` 을 통과하지만 비교가 언제나 거짓이라 "봤고 괜찮다"로 인증된다.
   * 음수 카운트는 규칙이 세는 축을 통째로 무의미하게 만든다. */
  for (const [name, mutate] of [
    ['today: NaN', (w: ConsoleFactsDto) => { w.outputs[0].today = NaN; }],
    ['today: 문자열', (w: ConsoleFactsDto) => { (w.outputs[0] as { today: unknown }).today = '16'; }],
    ['base: 음수', (w: ConsoleFactsDto) => { w.outputs[0].base = -1; }],
    ['attempts: 음수', (w: ConsoleFactsDto) => { w.tasks[0].attempts = -1; }],
    ['recordsOut: Infinity', (w: ConsoleFactsDto) => { w.tasks[0].recordsOut = Infinity; }],
    ['completenessExpected: 문자열', (w: ConsoleFactsDto) =>
      { (w.tasks[0] as { completenessExpected: unknown }).completenessExpected = '33'; }],
    ['boundary 음수', (w: ConsoleFactsDto) => { w.boundary.deliveryRows = -1; }],
  ] as const) {
    assert.equal(broken(mutate).ok, false, `${name} 을 통과시켰다`);
  }
});

test('거부 — 식별자·불리언 자리가 어긋나면 버린다', () => {
  /* 사건 식별자의 축이 문자열이 아니면 딥링크가 남의 사건을 열고, `required` 가 불리언이
   * 아니면 R05 의 필터가 뜻 없이 참이 된다. */
  for (const [name, mutate] of [
    ['runs[].id 가 수', (w: ConsoleFactsDto) => { (w.runs[0] as { id: unknown }).id = 1; }],
    ['tasks[].runId 가 null', (w: ConsoleFactsDto) => { (w.tasks[0] as { runId: unknown }).runId = null; }],
    ['required 가 문자열', (w: ConsoleFactsDto) => { (w.tasks[0] as { required: unknown }).required = 'true'; }],
    ['contract 가 수', (w: ConsoleFactsDto) => { (w.datasets[0] as { contract: unknown }).contract = 1; }],
    ['meta.today 가 null', (w: ConsoleFactsDto) => { (w.meta as { today: unknown }).today = null; }],
  ] as const) {
    assert.equal(broken(mutate).ok, false, `${name} 을 통과시켰다`);
  }
});

test('허용 — 정당한 null 은 거부하지 않는다', () => {
  /* 🔴 검증기가 과하면 **정상 응답을 통째로 버린다**. 비거래일 런의 거래일, 슬롯 키를 못 읽은
   * 레인, 기준 없는 산출은 전부 서버가 정당하게 내는 null 이다(B1). 거부하면 그날의 사고가
   * 화면에서 사라지는데, 그건 검증기가 만드는 거짓 안심이다. */
  const w = WIRE();
  w.runs[0].lane = null;
  w.runs[0].tradingDate = null;
  w.tasks[0].tradingDate = null;
  w.tasks[0].recordsOut = null;
  w.outputs[0].base = null;
  w.datasets[0].unverifiable = null;
  const r = parseFacts(w);
  assert.equal(r.ok, true, r.ok ? '' : `정상 응답을 거부했다: ${r.reason}`);
  if (!r.ok) return;
  assert.equal(r.facts.runs[0].lane, null);
  assert.equal(r.facts.outputs[0].base, null);
});

test('거부 사유는 어느 축인지 말한다 — 조회 실패 문장이 원인을 가리켜야 한다', () => {
  const r = broken((w) => { w.outputs[0].today = NaN; });
  assert.equal(r.ok, false);
  if (r.ok) return;
  assert.match(r.reason, /outputs/, `사유가 축을 안 가리킨다: ${r.reason}`);
});

test('검사표가 와이어 형상을 빠짐없이 덮는다 — 안 덮인 필드는 무검증으로 규칙에 흘러간다', () => {
  /* 🔴 부분만 검사하고 캐스트하면 이 경계가 약속한 "규칙은 자기 타입을 믿어도 된다"가 거짓이
   * 된다(리뷰가 잡았다 — `planned: "false"` 는 truthy 라 R01 이 거짓 P0 를 낸다).
   *
   * 손으로 유지되는 표는 반드시 낡으므로 **집합으로** 묶는다: 와이어 픽스처의 키가 표의 키에
   * 다 들어 있어야 한다. 서버 DTO 에 필드가 하나 늘고 픽스처가 따라가면 여기서 걸린다. */
  const w = WIRE();
  /* 양방향으로 본다 — 표에만 있고 픽스처에 없는 필드는 그 단언이 헛도는 자리이고, 서버가
   * 그 필드를 뺀 뒤에도 표에 남아 있으면 **정상 응답을 거부한다**(값이 `undefined` 가 된다).
   * 한 축만 검사하면 나머지 다섯의 드리프트를 못 잡는다(리뷰가 잡았다). */
  const both = (
    row: object,
    fields: Record<string, unknown>,
    axis: string,
    optional: string[] = [],
  ) => {
    const missing = Object.keys(row).filter((k) => !(k in fields));
    assert.deepEqual(missing, [], `${axis} 의 ${missing.join('·')} 가 검사표에 없다`);
    const unused = Object.keys(fields).filter((k) => !(k in row) && !optional.includes(k));
    assert.deepEqual(unused, [], `${axis} 픽스처가 ${unused.join('·')} 를 안 밟는다`);
  };
  /* `planned`·`noRunRow` 는 서버가 그 슬롯에만 싣는다 — 픽스처의 정상 런에는 없는 게 맞다. */
  both(w.runs[0], RUN_FIELDS, 'runs', ['planned', 'noRunRow']);
  both(w.tasks[0], TASK_FIELDS, 'tasks');
  both(w.datasets[0], DATASET_FIELDS, 'datasets');
  both(w.outputs[0], OUTPUT_FIELDS, 'outputs');
  both(w.boundary, BOUNDARY_FIELDS, 'boundary');
  both(w.chain.feeds[0], CHAIN_FEED_FIELDS, 'chain.feeds');
  both(w.chain.stages[0], CHAIN_STAGE_FIELDS, 'chain.stages');
  both(w.meta, META_FIELDS, 'meta');
});

test('🔴 거부 — 체인의 수 자리에 null 이 오면 버린다 (그 단계만 비교에서 사라진다)', () => {
  /* 이 축에는 `null` 자리가 없다 — 서버가 코호트를 정해 놓고 세므로 "못 셌다"가 없다.
   * 통과시키면 규칙이 그 단계만 조용히 건너뛰고, **손실이 "여기는 안 셌구나"로 접힌다**.
   * 갈래 하나만 망가진 응답도 같다: 나머지 갈래가 멀쩡해 화면은 정상으로 보인다. */
  for (const [name, mutate] of [
    ['stages[].batch 가 null', (w: ConsoleFactsDto) =>
      { (w.chain.stages[0] as { batch: unknown }).batch = null; }],
    ['stages[].intraday 가 null', (w: ConsoleFactsDto) =>
      { (w.chain.stages[0] as { intraday: unknown }).intraday = null; }],
    ['feeds[].v 가 null', (w: ConsoleFactsDto) => { (w.chain.feeds[0] as { v: unknown }).v = null; }],
    ['feeds[].v 가 NaN', (w: ConsoleFactsDto) => { w.chain.feeds[0].v = NaN; }],
    ['stages[].batch 가 음수', (w: ConsoleFactsDto) => { w.chain.stages[0].batch = -1; }],
    ['stages[].label 이 null', (w: ConsoleFactsDto) =>
      { (w.chain.stages[0] as { label: unknown }).label = null; }],
    ['chain 이 배열', (w: ConsoleFactsDto) => { (w as { chain: unknown }).chain = []; }],
    ['chain.stages 가 객체', (w: ConsoleFactsDto) => { (w.chain as { stages: unknown }).stages = {}; }],
    ['chain.stages 원소가 스칼라', (w: ConsoleFactsDto) =>
      { (w.chain.stages as unknown[])[0] = 1; }],
  ] as const) {
    assert.equal(broken(mutate).ok, false, `${name} 을 통과시켰다`);
  }
});

test('🔴 거부 — 갈래가 둘이 아닌 체인은 버린다 (소비자가 위치로 읽는다)', () => {
  /* `feeds[0]`=배치·`feeds[1]`=장중은 **위치 계약**이다(id 로 찾지 않는다). 한 갈래만 오면
   * 그 자리가 `undefined` 가 되어 그 갈래의 첫 비교점이 사라지고, 트리거→관측 사이의 손실이
   * 통째로 안 보인다 — 값이 틀리는 게 아니라 **묻는 것을 안 묻게** 된다. */
  assert.equal(broken((w) => { w.chain.feeds.pop(); }).ok, false, '갈래 하나짜리를 통과시켰다');
  assert.equal(broken((w) => { w.chain.feeds = []; }).ok, false, '빈 피드를 통과시켰다');
  assert.equal(broken((w) => { w.chain.stages = []; }).ok, false, '빈 단계 목록을 통과시켰다');
  assert.equal(
    broken((w) => { w.chain.feeds.push({ ...w.chain.feeds[0], id: 'feed.third' }); }).ok,
    false,
    '셋째 갈래를 통과시켰다 — 위치 계약이 무너진다',
  );
});

test('거부 — 안전 정수 범위를 넘은 건수는 이미 손상된 값이다', () => {
  /* `long` 은 2^53 을 넘을 수 있고 그때 `JSON.parse` 는 **반올림한 값**을 준다. `isInteger` 로만
   * 보면 그 손상된 값이 통과해, `expected`·`received` 가 1 차이인 응답이 같은 수가 되고
   * R07 이 결손을 정상으로 판정한다. */
  assert.equal(
    broken((w) => { w.tasks[0].completenessExpected = Number.MAX_SAFE_INTEGER + 2; }).ok,
    false,
    '안전 정수 범위를 넘은 건수를 통과시켰다',
  );
  const edge = WIRE();
  edge.tasks[0].completenessExpected = Number.MAX_SAFE_INTEGER;
  assert.equal(parseFacts(edge).ok, true, '안전 정수 상한 자체를 거부했다');
});

test('거부 — 무검증으로 새던 자리들', () => {
  /* 전수 검사 이전에 통과하던 것들이다. `planned: "false"` 가 대표적 — truthy 라 R01 이
   * "계획됐는데 런이 없다"로 읽어 거짓 P0 를 낸다. */
  for (const [name, mutate] of [
    ['planned: "false"', (w: ConsoleFactsDto) => { (w.runs[0] as { planned: unknown }).planned = 'false'; }],
    ['noRunRow: 1', (w: ConsoleFactsDto) => { (w.runs[0] as { noRunRow: unknown }).noRunRow = 1; }],
    ['ledgerStatus 가 객체', (w: ConsoleFactsDto) => { (w.runs[0] as { ledgerStatus: unknown }).ledgerStatus = {}; }],
    ['taskOutcome 가 객체', (w: ConsoleFactsDto) => { (w.tasks[0] as { taskOutcome: unknown }).taskOutcome = {}; }],
    ['stage 가 null', (w: ConsoleFactsDto) => { (w.tasks[0] as { stage: unknown }).stage = null; }],
    ['pipelineType 이 수', (w: ConsoleFactsDto) => { (w.tasks[0] as { pipelineType: unknown }).pipelineType = 1; }],
    ['expectedAsOf 가 수', (w: ConsoleFactsDto) => { (w.datasets[0] as { expectedAsOf: unknown }).expectedAsOf = 20260803; }],
    ['unit 이 null', (w: ConsoleFactsDto) => { (w.outputs[0] as { unit: unknown }).unit = null; }],
  ] as const) {
    assert.equal(broken(mutate).ok, false, `${name} 을 통과시켰다`);
  }
});

test('거부 — 건수 자리에 소수가 오면 버린다. 기준값의 소수는 정상이다', () => {
  /* 와이어에서 건수는 `long` 이라 소수가 올 수 없다. 반면 기준(중앙값)은 `Double` 이고 짝수
   * 표본이면 `.5` 가 정상이다 — 둘을 한 검사로 묶으면 한쪽이 반드시 틀린다. */
  assert.equal(broken((w) => { w.outputs[0].today = 0.5; }).ok, false, '건수에 소수를 통과시켰다');
  assert.equal(broken((w) => { w.tasks[0].attempts = 1.5; }).ok, false, '시도 수에 소수를 통과시켰다');
  assert.equal(broken((w) => { w.boundary.deliveryRows = 1.5; }).ok, false, '건수에 소수를 통과시켰다');
  const half = WIRE();
  half.outputs[0].base = 31.5;
  assert.equal(parseFacts(half).ok, true, '기준값의 소수를 거부했다 — 짝수 표본의 중앙값이다');
});

test('🔴 거부 — 날짜·시각이 파싱되지 않으면 버린다(포매터가 렌더를 죽인다)', () => {
  /* 문자열 여부만 보면 통과하고, 그 값은 곧장 `kst()` → `Intl` 로 가서
   * `RangeError: Invalid time value` 로 **렌더가 죽는다** — 응답 결함이 화면 단위 조회 실패가
   * 아니라 정체불명의 붕괴로 나오는 것이 이 경계가 존재하는 이유의 정반대다.
   * 조용히 틀리는 쪽도 있다: `tradingLag` 는 두 문자열을 사전순 비교해 형식이 깨지면 **지연 0**
   * 을 실측처럼 낸다. */
  const cases: [(w: ConsoleFactsDto) => void, string][] = [
    [(w) => { w.meta.today = '2026-8-3'; }, 'meta.today 미패딩'],
    [(w) => { w.meta.today = 'not-a-date'; }, 'meta.today 비날짜'],
    [(w) => { w.meta.db = 'not-an-instant'; }, 'meta.db 비시각'],
    [(w) => { w.runs[0].tradingDate = '08/03/2026'; }, 'runs[].tradingDate 다른 형식'],
    [(w) => { w.runs[0].deadline = '언젠가'; }, 'runs[].deadline'],
    [(w) => { w.tasks[0].tradingDate = '2026-08'; }, 'tasks[].tradingDate 절단'],
    [(w) => { w.datasets[0].expectedAsOf = '슬롯 창 08-02→08-03'; }, 'datasets[].expectedAsOf 표시 문자열'],
    [(w) => { w.datasets[0].collectedAt = 'x'; }, 'datasets[].collectedAt'],
    /* 🔴 `Date.parse` 를 게이트로 쓰면 이 셋이 통과한다 — 특히 마지막은 **3월 2일로 굴려**
     * 없는 날이 조용히 다른 실재 날이 되어 화면에 실측처럼 선다(실측). */
    [(w) => { w.meta.db = '2026'; }, '연도만'],
    [(w) => { w.meta.db = 'Aug 3 2026'; }, '영문 날짜'],
    [(w) => { w.meta.db = '2026-02-30T12:00Z'; }, '2월 30일 — 굴림'],
    [(w) => { w.runs[0].tradingDate = '2026-02-30'; }, '없는 날짜'],
    [(w) => { w.runs[0].tradingDate = '2025-02-29'; }, '평년 2월 29일'],
    [(w) => { w.runs[0].deadline = '2026-08-03T25:00:00+09:00'; }, '25시'],
    [(w) => { w.runs[0].deadline = '2026-08-03T12:61:00+09:00'; }, '61분'],
    /* 🔴 **소비자가 못 읽는 것은 받지 않는다.** 아래 셋은 `new Date()` 가 `Invalid Date` 를 주고
     * `kst()` 가 그 자리에서 `RangeError` 로 던진다(실측) — 통과시키면 사유 붙은 조회 실패가
     * 아니라 정체불명의 렌더 붕괴가 된다. 초 단위 오프셋은 Java 가 이론상 낼 수 있지만
     * 이 원장의 시각은 KST 세션이라 도달 경로가 없다. */
    [(w) => { w.meta.db = '2026-08-03T12:00:00+09:00:30'; }, '초 단위 오프셋'],
    [(w) => { w.runs[0].deadline = '2026-08-03T12:00:00+99:99'; }, '범위 밖 오프셋'],
    [(w) => { w.runs[0].ledgerUpdated = '2026-08-03T12:00:00+19:00'; }, '19시간 오프셋'],
    /* `ZoneOffset` 의 상한은 부호와 무관하게 **정확히 ±18:00** 이다 — `<= 18` 로 두면 이게 샌다 */
    [(w) => { w.runs[0].deadline = '2026-08-03T12:00:00+18:59'; }, '18:59 — 상한 밖'],
    [(w) => { w.meta.db = '2026-08-03T12:00:00-18:30'; }, '-18:30 — 상한 밖'],
  ];
  for (const [mutate, where] of cases) {
    assert.equal(broken(mutate).ok, false, `${where} 를 통과시켰다`);
  }
});

test('허용 — 서버가 실제로 내는 날짜 형식은 거부하지 않는다', () => {
  /* 과하면 정상 응답을 통째로 버린다. 서버는 `LocalDate.toString()`(YYYY-MM-DD)과
   * `OffsetDateTime.toString()` 을 내고, 후자는 오프셋·나노초 유무가 갈린다.
   * ⚠️ 통과시킨 값은 **`kst()` 가 실제로 그릴 수 있어야** 한다 — 아래 왕복 단언이 그 축이다. */
  const cases: [(w: ConsoleFactsDto) => void, string][] = [
    [(w) => { w.meta.db = '2026-08-03T16:20:34.112043+09:00'; }, '나노초 + 오프셋'],
    [(w) => { w.meta.db = '2026-08-03T07:21:16Z'; }, 'UTC Z'],
    [(w) => { w.runs[0].ledgerUpdated = null; }, 'null 시각'],
    [(w) => { w.runs[0].tradingDate = null; }, '비거래일 런의 null 거래일'],
    [(w) => { w.datasets[0].expectedAsOf = '2026-12-31'; }, '연말 날짜'],
    [(w) => { w.datasets[0].expectedAsOf = '2028-02-29'; }, '윤년 2월 29일'],
    /* Java `OffsetDateTime.toString()` 의 변형 — 초 생략 · 초 단위 오프셋.
     * `Date.parse` 는 뒤쪽에 `NaN` 을 주므로, 그걸 게이트로 쓰면 정상 응답을 버렸다. */
    [(w) => { w.runs[0].ledgerUpdated = '2026-08-03T16:20+09:00'; }, '초 생략'],
    [(w) => { w.runs[0].ledgerUpdated = '2026-08-03T16:20:34.1+09:00'; }, '나노초 1자리'],
    [(w) => { w.datasets[0].expectedAsOf = '0050-01-01'; }, '두 자리 연도 — Date.UTC 보정에 걸리던 자리'],
    [(w) => { w.meta.db = '2026-08-03T12:00:00-05:00'; }, '음수 오프셋'],
    [(w) => { w.meta.db = '2026-08-03T12:00:00+18:00'; }, '최대 오프셋(+)'],
    /* 부호를 안 보는 구현이라 지금은 둘 다 통과하지만, 부호별 분기가 생겨 한쪽만 막히는
     * 회귀가 나도 단언이 없으면 안 잡힌다 — 상한은 **부호와 무관하게** ±18:00 이다. */
    [(w) => { w.runs[0].deadline = '2026-08-03T12:00:00-18:00'; }, '최대 오프셋(−)'],
  ];
  for (const [mutate, where] of cases) {
    const r = broken(mutate);
    assert.equal(r.ok, true, `${where} 를 거부했다 — ${r.ok ? '' : r.reason}`);
  }
});

test('🔴 통과시킨 시각은 렌더러가 읽을 수 있다 — 문법만 맞고 못 그리면 거부보다 나쁘다', () => {
  /* 이 왕복이 없으면 "Java 가 낼 수 있으니 받자"로 문법을 넓히다 **소비자가 던지는 값**을
   * 그대로 통과시킨다(실제로 한 라운드 그랬다). 검사표를 넓히는 사람은 이 단언을 먼저 본다. */
  const r = parseFacts(WIRE());
  assert.equal(r.ok, true);
  if (!r.ok) return;
  const instants = [
    r.facts.meta.db,
    ...r.facts.runs.flatMap((x) => [x.ledger_updated, x.deadline]),
    ...r.facts.datasets.map((x) => x.collected_at),
  ].filter((x): x is string => typeof x === 'string');
  assert.ok(instants.length > 0, '검사할 시각이 픽스처에 없다 — 이 단언이 죽었다');
  for (const iso of instants) {
    assert.ok(!Number.isNaN(new Date(iso).getTime()), `렌더러가 못 읽는 값을 통과시켰다 — ${iso}`);
  }
});

/* ── 사실 축의 조회 상태 ──
 * 화면은 이 값 하나로 "표를 그릴지 / 스켈레톤을 세울지 / 조회 실패라 말할지"를 정한다.
 * 잘못 접으면 사실이 없는데 표가 서고, 빈 표는 그 자리에서 "위반 0건"으로 읽힌다. */
test('응답 결함은 조회 실패다 — 규칙별 못 돎이 아니라 화면 단위로 접는다', () => {
  const good = parseFacts(WIRE());
  const bad = parseFacts({});
  assert.equal(bad.ok, false, '전제: 빈 객체는 거부된다');

  assert.equal(factsAxis(null, false), 'pending', '응답 전 — 실패가 아니다');
  assert.equal(factsAxis(null, true), 'error');
  assert.equal(factsAxis(good, false), 'loaded');
  /* ⭐ 검증기가 거부한 응답을 `loaded` 로 두면 화면이 **빈 사실** 위에 표를 그린다 —
   * 그때 "런 0건 · 위반 0건"은 실측처럼 보이는 거짓이다. */
  assert.equal(factsAxis(bad, false), 'error', '거부된 응답은 실림이 아니다');
  assert.equal(factsAxis(bad, true), 'error');
  /* 직전 응답은 통과했는데 마지막 조회가 실패 — 판정은 서지만 낡았다(1분마다 도는 화면) */
  assert.equal(factsAxis(good, true), 'stale');
});

test('거부 — 응답 자체가 객체가 아니면 그 사실을 사유로 말한다', () => {
  /* 사유를 단언하지 않으면 이 가드는 **지워도 통과한다** — 배열·문자열은 뒤의 축 검사에서
   * 어차피 걸려서 거부 자체는 같기 때문이다. 그때 운영자가 받는 문장은 "runs 축이 배열이
   * 아니다"인데, 실제로는 응답이 통째로 다른 것이라 원인을 못 가리킨다. */
  for (const body of [null, undefined, 'x', 1, []]) {
    const r = parseFacts(body);
    assert.equal(r.ok, false, `${JSON.stringify(body)} 를 통과시켰다`);
    if (r.ok) continue;
    assert.match(r.reason, /응답이 객체가 아니다/, `${JSON.stringify(body)} 의 사유가 원인을 안 가리킨다`);
  }
});
