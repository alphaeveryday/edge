/* 사건 → 조사 경로 (ALPHA-738).
 *
 * 지키는 의도는 "모든 사건을 실행에 연결한다"가 **아니다**. 반대다 —
 *   · 위반이 실제로 들고 있는 식별자만 쓴다. 없으면 대상을 만들지 않는다.
 *   · 런 행이 없는 슬롯은 실행이 아니라 예정 슬롯이고, 원장 근거는 "행이 없다"까지다.
 *   · 큐·배포처럼 실행이 없는 사건은 실행 화면을 거치지 않고 원장 근거도 없다고 말한다.
 * 이 셋 중 하나라도 무너지면 무관한 최근 실행이 사건의 원인처럼 보인다.
 *
 * 실행: node --test src/pages/ops/investigation.test.ts
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { investigate, ledgerHref } from './investigation.ts';
import type { Facts, Incident, Violation } from '../../rules/types.ts';

const FACTS = {
  runs: [
    { id: 'etf-daily:2026-08-03T15:40', lane: 'etf-daily', kind: 'scheduled', trading_date: '2026-08-03' },
    { id: 'etf-daily:2026-07-28T15:40', lane: 'etf-daily', kind: 'scheduled', trading_date: '2026-07-28', planned: true, no_run_row: true },
  ],
  tasks: [
    {
      task_key: 'INVESTOR_COLLECTION_KIS',
      run_id: 'etf-daily:2026-08-03T15:40',
      pipeline_type: 'etf-daily',
      stage: 'raw',
      dataset: 'investor_flow',
      required: true,
      task_outcome: 'FULFILLED',
    },
  ],
  meta: { db: '', aws: '', today: '2026-08-03' },
} as unknown as Facts;

const violation = (o: Partial<Violation>): Violation =>
  ({
    vid: 'R99#0',
    rule: 'R99',
    ruleName: '테스트',
    layer: '런',
    kls: '고장',
    sev: 'P0',
    dep: null,
    target: 't',
    title: '제목',
    metric: 1,
    unit: '건',
    why: '',
    evidence: '',
    drill: ['run', 'run-etf-daily:2026-08-03T15:40'],
    ...o,
  }) as Violation;

const incident = (v: Violation): Incident => ({ root: v, members: [], sev: v.sev, size: 1 });

test('런 축 사건은 그 런만 연다 — 최근 런 전체를 다시 훑게 하지 않는다', () => {
  const r = investigate(incident(violation({})), FACTS);
  assert.equal(r.targets.length, 1);
  assert.equal(r.targets[0].kind, 'run');
  /* 런 하나는 자기 페이지를 갖는다 — 목록의 선택 상태(?run_id=)가 아니라 경로로 지목한다 */
  assert.match(r.targets[0].href, /^\/ops\/runs\/etf-daily%3A2026-08-03T15%3A40(\?|$)/);
  assert.match(r.targets[0].href, /fromIncident=R99%230/);
  assert.deepEqual(r.ledger, { incident: 'R99#0', runKey: 'etf-daily:2026-08-03T15:40' });
});

test('작업 축은 위반이 기록한 run_id 로만 연다 — 없으면 런을 추측하지 않는다', () => {
  const withRun = investigate(
    incident(violation({ drill: ['run', 'task-INVESTOR_COLLECTION_KIS'], runId: 'etf-daily:2026-08-03T15:40' })),
    FACTS,
  );
  assert.equal(withRun.targets[0].kind, 'run');
  /* 원장 문맥에 작업·데이터셋까지 실린다 — 원장이 그 범위로 좁혀야 근거가 된다 */
  assert.deepEqual(withRun.ledger, {
    incident: 'R99#0',
    runKey: 'etf-daily:2026-08-03T15:40',
    task: 'INVESTOR_COLLECTION_KIS',
    dataset: 'investor_flow',
  });

  const noRun = investigate(incident(violation({ drill: ['run', 'task-INVESTOR_COLLECTION_KIS'] })), FACTS);
  assert.deepEqual(noRun.targets, [], '런을 모르면 실행 화면으로 보내지 않는다');
  assert.equal(noRun.ledger, null);
  assert.match(noRun.ledgerNote ?? '', /추측해 연결하지 않는다/);
});

test('런 행이 없는 슬롯은 실행이 아니라 예정 슬롯이고, 원장 근거는 "행 없음"까지다', () => {
  const r = investigate(
    incident(violation({ drill: ['run', 'run-etf-daily:2026-07-28T15:40'] })),
    FACTS,
  );
  assert.equal(r.targets[0].kind, 'slot');
  assert.deepEqual(r.ledger, { incident: 'R99#0', runKey: 'etf-daily:2026-07-28T15:40' });
  /* 작업·시도 행이 있는 것처럼 보이면 안 된다 */
  assert.match(r.ledgerNote ?? '', /행이 없다/);
});

test('큐 사건은 실행 화면을 거치지 않고 원장 근거도 없다고 말한다', () => {
  const r = investigate(
    incident(violation({ layer: '큐', drill: ['chain', 'q-price-explanation-realtime'], target: 'price-explanation-realtime' })),
    FACTS,
  );
  assert.equal(r.targets[0].kind, 'queue');
  assert.doesNotMatch(r.targets[0].href, /\/ops\/runs/);
  assert.equal(r.ledger, null, '없는 원장 근거를 만들지 않는다');
  assert.equal(ledgerHref(r.ledger), null);
});

test('실시간 데이터셋 사건은 1분 창이 아니라 그 날짜의 세션을 연다', () => {
  const r = investigate(incident(violation({ drill: ['dataset', 'ds-price_minute'] })), FACTS);
  assert.equal(r.targets[0].kind, 'session');
  assert.equal(r.targets[0].href, '/minute?date=2026-08-03&dataset=price_minute');
  assert.deepEqual(r.ledger, { incident: 'R99#0', dataset: 'price_minute', date: '2026-08-03' });
});

test('배치 데이터셋 사건은 실행에 매이지 않는다 — 원장을 런까지 좁히지 않는다', () => {
  const r = investigate(incident(violation({ drill: ['dataset', 'ds-investor_flow'] })), FACTS);
  assert.equal(r.targets[0].kind, 'dataset');
  assert.equal(r.ledger?.runKey, undefined, '런 키를 지어내지 않는다');
  assert.match(r.ledgerNote ?? '', /실행에 매여 있지 않아/);
});

test('원장 주소는 문맥이 있을 때만 만든다 — 문맥 없는 원장 열기를 만들지 않는다', () => {
  assert.equal(ledgerHref(null), null);
  assert.equal(ledgerHref({}), null);
  assert.equal(
    ledgerHref({ incident: 'R07#0', runKey: 'etf-daily:2026-08-03T15:40', task: 'A', dataset: 'd' }),
    '/sources?incident=R07%230&runKey=etf-daily%3A2026-08-03T15%3A40&task=A&dataset=d',
  );
});
