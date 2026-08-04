/* 장중 1분 표현 모델 테스트 (ALPHA-738).
 *
 * 이 테스트가 지키는 의도는 배치가 아니라 **의미**다:
 *   · 무증거(안 돌았다)와 정상·빈 데이터(돌았는데 거래가 없었다)는 끝까지 다른 사실로 남는다.
 *   · 서버 판정(leaseExpired · overdueNoEvidence)을 화면이 다시 정의하지 않는다.
 *   · 응답이 못 가르는 것(도래 여부)을 숫자로 지어내지 않는다.
 *
 * 실행: node --test src/domains/sources/minuteView.test.ts
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  evidencedCount,
  gapRuns,
  issues,
  liveness,
  materializedCount,
  qualityDefectCount,
  segments,
} from './minuteView.ts';
import type { MinuteGapWindow, MinuteJobCounts, MinuteSession } from './types.ts';

const NO_JOBS: MinuteJobCounts = { waiting: 0, claimed: 0, claimedExpired: 0, succeeded: 0, dead: 0 };

type SessionOverride = Omit<Partial<MinuteSession>, 'windows'> & {
  windows?: Partial<MinuteSession['windows']>;
};

const session = (o: SessionOverride = {}): MinuteSession => ({
  sessionId: 's1',
  dataset: 'price_minute',
  sourceGroup: 'KRX',
  phase: 'ACTIVE',
  universeVersion: 'v1',
  expectedWindowCount: 10,
  processedThrough: null,
  contiguousCompleteThrough: null,
  heartbeatAt: null,
  leaseExpiresAt: null,
  leaseExpired: false,
  gaps: [],
  priceJobs: NO_JOBS,
  ...o,
  windows: {
    due: 0,
    claimed: 0,
    valid: 0,
    validEmpty: 0,
    incomplete: 0,
    missing: 0,
    invalid: 0,
    overdueNoEvidence: 0,
    ...o.windows,
  },
});

const gap = (start: string, end: string, dataStatus: string, noEvidence: boolean): MinuteGapWindow => ({
  windowStart: `2026-08-03T${start}:00+09:00`,
  windowEnd: `2026-08-03T${end}:00+09:00`,
  dataStatus: dataStatus as MinuteGapWindow['dataStatus'],
  noEvidence,
});

/* ── 이 화면의 핵심 계약 ── */

test('무증거와 정상·빈 데이터는 서로 다른 조각으로 남는다 (합쳐 세지 않는다)', () => {
  const s = session({ expectedWindowCount: 8, windows: { valid: 3, validEmpty: 4, overdueNoEvidence: 1, due: 1 } });
  const segs = segments(s);
  const empty = segs.find((x) => x.key === 'validEmpty')!;
  const none = segs.find((x) => x.key === 'noEvidence')!;
  assert.ok(empty && none, '두 조각이 모두 있어야 한다');
  assert.equal(empty.count, 4);
  assert.equal(none.count, 1);
  // 라벨·무늬·톤 어느 축으로도 겹치지 않는다 — 색 하나로만 갈리면 흑백에서 같은 사실이 된다
  assert.notEqual(empty.label, none.label);
  assert.notEqual(empty.pattern, none.pattern);
  assert.notEqual(empty.tone, none.tone);
});

test('정상·빈 데이터만 있고 무증거가 0이면 무증거 항목은 뜨지 않는다', () => {
  const s = session({ expectedWindowCount: 5, windows: { validEmpty: 5 } });
  assert.equal(issues(s, NO_JOBS).some((i) => i.key === 'noEvidence'), false);
  assert.equal(segments(s).some((x) => x.key === 'noEvidence'), false);
});

test('무증거는 증거 있는 창·품질 결함 어느 쪽에도 섞이지 않는다', () => {
  const s = session({
    expectedWindowCount: 10,
    windows: { valid: 3, validEmpty: 2, incomplete: 1, invalid: 1, missing: 1, overdueNoEvidence: 2 },
  });
  assert.equal(evidencedCount(s), 7); // valid+validEmpty+incomplete+invalid — 무증거·MISSING 제외
  assert.equal(qualityDefectCount(s), 3); // incomplete+invalid+missing — 무증거 제외
});

/* ── 응답이 못 가르는 것 ── */

test('미도래와 수집 중은 한 조각으로 두고 진행률 분모를 만들지 않는다', () => {
  /* due 6 + claimed 2 중 무증거 1 — 나머지 7 은 미도래인지 수집 중인지 응답이 가르지 않는다 */
  const s = session({ expectedWindowCount: 10, windows: { valid: 2, due: 6, claimed: 2, overdueNoEvidence: 1 } });
  const pending = segments(s).find((x) => x.key === 'pending')!;
  assert.equal(pending.count, 7);
  assert.match(pending.label, /미도래/);
  // 조각의 합은 언제나 기대 창 수다 — 없는 분모를 만들지 않는다
  assert.equal(segments(s).reduce((a, x) => a + x.count, 0), s.expectedWindowCount);
});

test('DEAD 는 해소 축이 없다는 사실을 제목에 남기고 차단으로 단정하지 않는다', () => {
  const dead = issues(session(), { ...NO_JOBS, dead: 3 }).find((i) => i.key === 'dead')!;
  assert.equal(dead.count, 3);
  assert.match(dead.title, /해소 여부 미기록/);
  assert.notEqual(dead.tone, 'blocked');
});

/* ── 서버 판정을 재정의하지 않는다 ── */

test('실행 생존은 phase 를 먼저 본다 — 종료 국면의 lease 만료는 정상이다', () => {
  assert.equal(liveness(session({ phase: 'FINALIZED', leaseExpired: true })).kind, 'closing');
  assert.equal(liveness(session({ phase: 'DRAINED', leaseExpired: true })).kind, 'closing');
  assert.equal(liveness(session({ phase: 'ACTIVE', leaseExpired: true })).kind, 'broken');
  assert.equal(liveness(session({ phase: 'ACTIVE', leaseExpired: false })).kind, 'live');
  /* null 은 만료(실행체 증거 끊김)와 다른 사실이다 — 뭉개면 미기동이 장애로 보인다 */
  assert.equal(liveness(session({ phase: 'ACTIVE', leaseExpired: null })).kind, 'unknown');
});

test('종료 국면 세션은 lease 만료만으로 확인 항목이 되지 않는다', () => {
  assert.equal(
    issues(session({ phase: 'FINALIZED', leaseExpired: true }), NO_JOBS).some((i) => i.key === 'liveness'),
    false,
  );
});

/* ── 항등식 경고 ── */

test('기대 창 수와 실재 행 수가 다르면 원장 불일치를 올린다', () => {
  const s = session({ expectedWindowCount: 10, windows: { valid: 4 } });
  assert.equal(materializedCount(s), 4);
  const m = issues(s, NO_JOBS).find((i) => i.key === 'ledgerMismatch')!;
  assert.equal(m.count, 6);
  const gapSeg = segments(s).find((x) => x.key === 'unmaterialized')!;
  assert.equal(gapSeg.count, 6);
});

test('기대 창 수와 실재 행 수가 같으면 불일치 항목이 없다', () => {
  const s = session({ expectedWindowCount: 4, windows: { valid: 3, due: 1 } });
  assert.equal(issues(s, NO_JOBS).some((i) => i.key === 'ledgerMismatch'), false);
});

/* ── 결손 구간 ── */

test('연속한 같은 상태의 창만 한 구간으로 접는다', () => {
  const runs = gapRuns([
    gap('10:14', '10:15', 'DUE', true),
    gap('10:15', '10:16', 'DUE', true),
    gap('11:02', '11:03', 'CLAIMED', true),
  ]);
  assert.equal(runs.length, 2);
  assert.equal(runs[0].count, 2);
  assert.equal(runs[0].from.slice(11, 16), '10:14');
  assert.equal(runs[0].to.slice(11, 16), '10:16');
  assert.equal(runs[1].count, 1);
});

test('맞닿아 있어도 상태가 다르면 절대 합치지 않는다', () => {
  const runs = gapRuns([
    gap('09:37', '09:38', 'INCOMPLETE', false),
    gap('09:38', '09:39', 'DUE', true), // 무증거 — 붙어 있어도 다른 사실
  ]);
  assert.equal(runs.length, 2);
  assert.equal(runs[0].noEvidence, false);
  assert.equal(runs[1].noEvidence, true);
});

test('확인 항목의 시각 범위는 그 항목의 창에서만 나온다', () => {
  const s = session({
    expectedWindowCount: 4,
    windows: { valid: 1, incomplete: 1, overdueNoEvidence: 2 },
    gaps: [
      gap('09:37', '09:38', 'INCOMPLETE', false),
      gap('13:41', '13:42', 'CLAIMED', true),
      gap('10:14', '10:15', 'DUE', true),
    ],
  });
  const list = issues(s, NO_JOBS);
  const none = list.find((i) => i.key === 'noEvidence')!;
  const quality = list.find((i) => i.key === 'quality')!;
  assert.equal(none.range!.from.slice(11, 16), '10:14');
  assert.equal(none.range!.to.slice(11, 16), '13:42');
  // 품질 결함 범위에 무증거 창 시각이 섞이지 않는다
  assert.equal(quality.range!.from.slice(11, 16), '09:37');
  assert.equal(quality.range!.to.slice(11, 16), '09:38');
});

test('좁은 표면용 짧은 이름은 제목을 자르지 않고 따로 준다', () => {
  /* 첫 화면 한 줄은 제목을 잘라 쓰면 괄호가 반쯤 남는다 — 별도 필드로 짧게 준다 */
  /* 기대 창 수와 실재 행 수를 맞춰 둔다 — 안 맞으면 원장 불일치까지 끼어 의도가 흐려진다 */
  const s = session({ expectedWindowCount: 1, windows: { due: 1, overdueNoEvidence: 1 } });
  const list = issues(s, { ...NO_JOBS, dead: 1 });
  assert.deepEqual(list.map((i) => i.short), ['무증거', 'DEAD job']);
  for (const i of list) assert.ok(i.short.length < i.title.length);
});

test('정상 세션에는 확인할 항목이 하나도 없다', () => {
  const s = session({ expectedWindowCount: 5, windows: { valid: 4, validEmpty: 1 } });
  assert.deepEqual(issues(s, { ...NO_JOBS, succeeded: 120 }), []);
});
