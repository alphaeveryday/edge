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
  datasetKind,
  evidencedCount,
  gapRuns,
  hasNoSignal,
  issues,
  liveness,
  materializedCount,
  qualityDefectCount,
  segments,
  sessionHealth,
  windowUnit,
} from './minuteView.ts';
import type { MinuteGapWindow, MinuteJobCounts, MinuteSession } from './types.ts';

const NO_JOBS: MinuteJobCounts = { waiting: 0, claimed: 0, claimedExpired: 0, succeeded: 0, dead: 0 };

type SessionOverride = Omit<Partial<MinuteSession>, 'windows'> & {
  windows?: Partial<MinuteSession['windows']>;
};

const session = (o: SessionOverride = {}): MinuteSession => ({
  sessionId: 's1',
  dataset: 'price_minute',
  /* 어휘 정본(`states.py` `SOURCE_GROUPS_BY_DATASET`) — 어휘 밖 값을 쓰면 초록이
   * 프로덕션 상태를 증명하지 못한다 */
  sourceGroup: 'kis',
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

/* ── 데이터셋은 서로 다른 판정 단위다 (장중은 시간대이지 판정 단위가 아니다) ── */

const newsSession = (o: SessionOverride = {}) =>
  session({ dataset: 'news_minute', sourceGroup: 'bigkinds', universeVersion: 'none', ...o });

test('원장 dataset 어휘를 그대로 가른다 — 모르는 값을 가격으로 접지 않는다', () => {
  assert.equal(datasetKind('price_minute'), 'price');
  assert.equal(datasetKind('news_minute'), 'news');
  assert.equal(datasetKind('price_minute_v2'), 'other');
  assert.equal(windowUnit('news'), 'poll(1분)');
  assert.notEqual(windowUnit('price'), windowUnit('news'));
});

test('뉴스의 빈 결과는 "거래 없음"이 아니라 "신규 기사 0건"이다', () => {
  /* 같은 컬럼이 다른 사실을 말한다 — 가격 문구를 뉴스에 그대로 쓰면 없는 의미가 붙는다 */
  const w = { valid: 3, validEmpty: 5 };
  const priceEmpty = segments(session({ expectedWindowCount: 8, windows: w })).find(
    (x) => x.key === 'validEmpty',
  )!;
  const newsEmpty = segments(newsSession({ expectedWindowCount: 8, windows: w })).find(
    (x) => x.key === 'validEmpty',
  )!;
  assert.match(priceEmpty.meaning, /거래가 없었/);
  assert.match(newsEmpty.meaning, /신규 기사가 없었/);
  assert.doesNotMatch(newsEmpty.meaning, /거래/);
  /* 카운트 축은 그대로다 — 의미만 갈린다(서버 판정을 화면이 다시 만들지 않는다) */
  assert.equal(priceEmpty.count, newsEmpty.count);
});

test('공시는 poll 레인이다 — 카탈로그가 poll 이라 부르는데 화면이 창이라 부르면 안 된다', () => {
  /* disclosure_minute 는 증분 커서가 없어 매 tick 이 날짜창 전체를 다시 읽는다 — window 는
   * 산출 단위가 아니라 "그 분에 한 번 폴링했다"는 원장 단위다(states.py). 카탈로그도 '1분
   * poll' 로 노출한다. 여기서 'other' 로 떨어지면 단위·문구가 가격 어휘로 돌아간다. */
  assert.equal(datasetKind('disclosure_minute'), 'disclosure');
  assert.equal(windowUnit('disclosure'), windowUnit('news'), 'poll 레인은 단위가 같다');

  const w = { valid: 3, validEmpty: 5 };
  const s0 = session({ dataset: 'disclosure_minute', expectedWindowCount: 8, windows: w });
  const empty = segments(s0).find((x) => x.key === 'validEmpty')!;
  assert.match(empty.meaning, /신규 공시가 없었/);
  assert.doesNotMatch(empty.meaning, /거래/, '가격 어휘가 새면 안 된다');
  assert.doesNotMatch(empty.meaning, /기사/, '뉴스 표를 그대로 빌려 쓰면 안 된다');

  /* ⚠️ 따라잡기(anchor 미도달)는 **뉴스 worker 고유**다 — 공시엔 그 기전이 없다.
   * poll 이라는 이유로 뉴스 문구를 통째로 물려주면 없는 기전이 화면에 선다. */
  const q = issues(
    session({ dataset: 'disclosure_minute', expectedWindowCount: 4, windows: { valid: 3, invalid: 1 } }),
    NO_JOBS,
  ).find((x) => x.key === 'quality')!;
  assert.match(q.title, /poll/);
  assert.doesNotMatch(q.detail, /anchor/, '공시에 anchor 따라잡기는 없는 기전이다');
  assert.doesNotMatch(q.title, /창/);
});

test('어휘 밖 dataset 의 빈 결과에 "거래 없음"을 붙이지 않는다 — 가른 뒤 도로 접지 않는다', () => {
  /* `datasetKind` 가 other 로 가르는 이유가 "모르는 것을 가격으로 접으면 없는 의미가 붙는다"
   * 인데, 문구 층이 뉴스만 덮어쓰면 other 는 가격 문구로 되돌아간다. 새 분 데이터셋
   * (inav·업종지수)이 붙는 날 그 축의 사실을 모른 채 거래를 말하게 된다. */
  const w = { valid: 3, validEmpty: 5 };
  const otherEmpty = segments(
    session({ dataset: 'inav_minute', expectedWindowCount: 8, windows: w }),
  ).find((x) => x.key === 'validEmpty')!;
  assert.doesNotMatch(otherEmpty.meaning, /거래/, '모르는 데이터셋에 거래를 단정하면 안 된다');
  assert.doesNotMatch(otherEmpty.meaning, /기사/, '뉴스 문구를 빌려와서도 안 된다');
  /* 정상 귀결이라는 사실 자체는 유지된다 — 판정을 약화시키는 게 아니라 근거만 중립화한다 */
  assert.equal(otherEmpty.tone, 'active');
  assert.equal(otherEmpty.count, 5);

  /* 원장 컬럼 그대로인 조각은 덮어쓰지 않는다 — 불필요한 갈래를 만들지 않았는지 고정한다 */
  const otherValid = segments(
    session({ dataset: 'inav_minute', expectedWindowCount: 8, windows: w }),
  ).find((x) => x.key === 'valid')!;
  const priceValid = segments(session({ expectedWindowCount: 8, windows: w })).find(
    (x) => x.key === 'valid',
  )!;
  assert.equal(otherValid.meaning, priceValid.meaning);
});

test('뉴스의 불완전은 unit 유실이 아니라 anchor 미도달(따라잡기)이다', () => {
  const inc = segments(newsSession({ expectedWindowCount: 4, windows: { valid: 3, incomplete: 1 } })).find(
    (x) => x.key === 'incomplete',
  )!;
  assert.match(inc.label, /따라잡기/);
  assert.match(inc.meaning, /anchor/);
  /* 뒤처짐을 성공으로 위장하지 않는다 — 정상 조각과 톤이 갈린다 */
  assert.notEqual(inc.tone, 'active');
});

test('뉴스 확인 항목의 단위는 창이 아니라 poll 이다', () => {
  const s = newsSession({
    expectedWindowCount: 6,
    windows: { valid: 2, validEmpty: 2, incomplete: 1, due: 1, overdueNoEvidence: 1 },
  });
  const list = issues(s, NO_JOBS);
  const none = list.find((i) => i.key === 'noEvidence')!;
  assert.equal(none.unit, 'poll(1분)');
  assert.match(none.title, /poll/);
  for (const i of list) assert.doesNotMatch(i.unit, /창/);
});

/* ── 무증거는 판정이지 원인이 아니다 ── */

test('무증거 근거는 서버 판정 조건으로 쓰고 원인을 단정하지 않는다', () => {
  const w = { valid: 2, due: 2, overdueNoEvidence: 2 };
  for (const s of [
    session({ expectedWindowCount: 4, windows: w }),
    newsSession({ expectedWindowCount: 4, windows: w }),
  ]) {
    const none = issues(s, NO_JOBS).find((i) => i.key === 'noEvidence')!;
    /* 서버가 실제로 쓰는 술어(기한 경과 + DUE/유효 lease 없는 CLAIMED)를 그대로 적는다 */
    assert.match(none.detail, /기한\(window_end\) 경과 후 결과 증거 없음/);
    assert.match(none.detail, /DUE 또는 유효 lease 없는 CLAIMED/);
    /* VALID_EMPTY 는 실행 증거가 있으므로 정상 귀결로 따로 센다는 사실이 함께 남는다 */
    assert.match(none.detail, /VALID_EMPTY/);
    assert.match(none.detail, /다음 확인/);
    /* task/attempt 근거 없이 실행체 사망·미실행을 확정하지 않는다 */
    for (const banned of ['안 돌았', '죽었', '죽은', '사망']) {
      assert.ok(!none.detail.includes(banned), `근거에 원인 단정이 남았다: ${banned}`);
    }
  }
});

test('lease 만료는 별도 사실로 남기되 실행체 사망으로 읽히지 않는다', () => {
  const live = liveness(session({ phase: 'ACTIVE', leaseExpired: true }));
  assert.equal(live.kind, 'broken');
  assert.match(live.basis, /lease 만료/);
  assert.match(live.basis, /서버\(DB 시계\) 판정/);
  /* 원인은 이 응답이 답하지 않는다는 사실을 함께 적는다 */
  assert.match(live.basis, /이 응답이 답하지 않는다/);
});

test('유효 lease 없는 claim 은 고착 후보이지 consumer 사망 확정이 아니다', () => {
  const stuck = issues(session(), { ...NO_JOBS, claimedExpired: 2 }).find(
    (i) => i.key === 'claimedExpired',
  )!;
  assert.match(stuck.detail, /consumer 사망 확정이 아니다/);
});

test('무증거 판정 자체는 데이터셋과 무관하게 서버 카운트 그대로다', () => {
  /* 뉴스라고 관대해지지 않는다 — 이벤트가 없었다는 이유로 무증거를 지우면 결손이 사라진다 */
  const w = { valid: 2, due: 2, overdueNoEvidence: 2 };
  const price = issues(session({ expectedWindowCount: 4, windows: w }), NO_JOBS);
  const news = issues(newsSession({ expectedWindowCount: 4, windows: w }), NO_JOBS);
  assert.equal(news.find((i) => i.key === 'noEvidence')!.count, 2);
  assert.equal(
    price.find((i) => i.key === 'noEvidence')!.count,
    news.find((i) => i.key === 'noEvidence')!.count,
  );
});

/* ── 세션 건강도 — 네 축을 합치되 이유를 잃지 않는다 ── */

test('수집기가 살아 있어도 품질 결함이 있으면 정상이 아니다', () => {
  const s = session({
    expectedWindowCount: 390,
    windows: { valid: 280, validEmpty: 40, incomplete: 3, due: 67 },
    leaseExpired: false,
  });
  const h = sessionHealth(s, NO_JOBS);
  assert.equal(h.kind, 'caution');
  assert.match(h.reason, /품질 결함 3/);
  /* 축이 따로 남아 원인을 볼 수 있다 */
  assert.match(h.liveness, /수집기 정상/);
  assert.equal(h.quality.defects, 3);
});

test('커버리지 분모는 기한 도래분이다 — 미도래 창을 결손으로 세지 않는다', () => {
  const s = session({
    expectedWindowCount: 390,
    /* 도래: 증거 323 + 무증거 0 = 323. 미도래·수집 중 67 은 분모 밖 */
    windows: { valid: 280, validEmpty: 40, incomplete: 3, due: 67 },
  });
  const h = sessionHealth(s, NO_JOBS);
  assert.equal(h.coverage.elapsed, 323);
  assert.equal(h.coverage.evidenced, 323);
  assert.ok(h.coverage.elapsed < s.expectedWindowCount, '거래일 전체 기대 창을 분모로 쓰지 않는다');
});

test('heartbeat 끊김·기한 경과 무증거는 장애다', () => {
  assert.equal(sessionHealth(session({ leaseExpired: true }), NO_JOBS).kind, 'failure');
  const noEv = sessionHealth(
    session({ expectedWindowCount: 10, windows: { valid: 6, due: 4, overdueNoEvidence: 4 } }),
    NO_JOBS,
  );
  assert.equal(noEv.kind, 'failure');
  assert.match(noEv.reason, /기한 경과 후 결과 증거 없음 4/);
});

test('세션 시작 전은 대기, 종료 국면은 종료 — 결손으로 그리지 않는다', () => {
  const wait = sessionHealth(session({ phase: 'PLANNED', expectedWindowCount: 390, windows: { due: 390 } }), NO_JOBS);
  assert.equal(wait.kind, 'waiting');
  const closed = sessionHealth(
    session({ phase: 'FINALIZED', leaseExpired: true, contiguousCompleteThrough: 'X', windows: { valid: 390 } }),
    NO_JOBS,
  );
  assert.equal(closed.kind, 'closed');
  assert.match(closed.reason, /최종 연속 완결/);
});

test('큐 고착·DEAD 도 주의로 올라온다 — heartbeat 만으로 정상을 정하지 않는다', () => {
  const s = session({ expectedWindowCount: 5, windows: { valid: 5 } });
  assert.equal(sessionHealth(s, NO_JOBS).kind, 'normal');
  assert.equal(sessionHealth(s, { ...NO_JOBS, dead: 2 }).kind, 'caution');
  assert.equal(sessionHealth(s, { ...NO_JOBS, claimedExpired: 1 }).kind, 'caution');
});

test('MISSING 은 기한이 지난 창이다 — 커버리지 분모에서 빼면 만점으로 보인다', () => {
  /* EOD reconciliation 이 결손으로 판정한 창은 기한이 확실히 지났다. 분모에서 빼면
   * `기한 도래 N 중 증거 N` 이 되어 커버리지가 만점인데, 같은 창을 품질 결함이 세고 있다 —
   * 한 화면이 같은 창을 두 번 다르게 말한다. */
  const s0 = session({ expectedWindowCount: 390, windows: { valid: 389, missing: 1 } });
  const h = sessionHealth(s0, NO_JOBS);
  assert.equal(h.coverage.evidenced, 389);
  assert.equal(h.coverage.elapsed, 390, 'MISSING 도 도래한 창이다');
  assert.notEqual(h.coverage.elapsed, h.coverage.evidenced, '결함이 있는데 만점으로 서면 안 된다');
  assert.equal(h.quality.defects, 1, '같은 창을 결함으로도 센다');
});

test('공시 품질 결함은 부분 실패(INCOMPLETE)를 빠뜨리지 않는다 — 없는 원인만 대면 다른 데를 본다', () => {
  /* `commit_disclosure_window` 는 하위 스텝 부분 실패를 INCOMPLETE 로 커밋한다.
   * `qualityDefectCount` 는 그걸 세는데 문구가 격리·MISSING 만 대면, INCOMPLETE 뿐인
   * 세션에서 운영자가 실제로 실패한 체인 스텝이 아닌 곳을 찾게 된다. */
  const q = issues(
    session({ dataset: 'disclosure_minute', expectedWindowCount: 4, windows: { valid: 3, incomplete: 1 } }),
    NO_JOBS,
  ).find((x) => x.key === 'quality')!;
  assert.equal(q.count, 1);
  assert.match(q.title, /부분 실패/, '셀 수 있는 원인을 문구가 빠뜨리면 안 된다');
  assert.match(q.detail, /INCOMPLETE/);
  assert.doesNotMatch(q.detail, /anchor/, '뉴스 고유 기전은 여전히 안 붙는다');
});

test('마감 세션이 결함을 안고 있으면 깨끗한 종료로 그리지 않는다', () => {
  /* 종료 국면 분기는 결함 검사보다 먼저 반환한다 — 결함을 안 보면 EOD 가 결손을 판정한
   * 뒤(가장 확정적인 결함)인데도 배지가 경고 없이 선다. 국면은 종료 그대로 두고 톤·사유로
   * 드러낸다: 국면을 장애로 바꾸면 서버가 안 한 판정을 화면이 만든다. */
  const dirty = sessionHealth(
    session({ phase: 'FINALIZED', expectedWindowCount: 390, windows: { valid: 389, missing: 1 } }),
    NO_JOBS,
  );
  assert.equal(dirty.kind, 'closed', '국면은 종료 그대로');
  assert.equal(dirty.tone, 'warn', '결함이 있는데 경고 없는 톤이면 안 된다');
  assert.match(dirty.reason, /남은 결함 1/);

  const clean = sessionHealth(
    session({ phase: 'FINALIZED', expectedWindowCount: 390, windows: { valid: 390 } }),
    NO_JOBS,
  );
  assert.equal(clean.tone, 'gated', '결함이 없으면 종전대로');
  assert.doesNotMatch(clean.reason, /남은 결함/);

  /* 창은 멀쩡한데 job 만 고착된 마감 세션 — 이 분기가 고착 검사보다 먼저 반환하므로
   * 창 축만 세면 깨끗하게 선다. 같은 세션의 issues() 는 그 job 을 드러내고 있다. */
  const stuckOnly = session({ phase: 'FINALIZED', expectedWindowCount: 390, windows: { valid: 390 } });
  const jobs = { ...NO_JOBS, claimedExpired: 1, dead: 2 };
  const h = sessionHealth(stuckOnly, jobs);
  assert.equal(h.tone, 'warn', '고착 job 이 있는데 깨끗한 종료로 서면 안 된다');
  assert.match(h.reason, /고착 job 3/);
  assert.doesNotMatch(h.reason, /남은 결함/, '창 결함은 없으니 그 문구는 안 붙는다');
  assert.ok(issues(stuckOnly, jobs).length > 0, '같은 세션의 확인 항목은 그 job 을 드러낸다');
});

test('원장 불일치는 요약에도 뜬다 — 상세만 "못 믿는다"고 하고 요약이 정상이면 안 된다', () => {
  /* 기대 390인데 행이 389면 `issues()` 가 원장 불일치를 낸다. `sessionHealth` 가 그걸 안 보면
   * 요약은 "정상", 상세는 "원장 수를 그대로 믿으면 안 된다" 로 갈린다. 장애로는 안 세운다 —
   * 세션이 죽은 게 아니라 셈의 근거가 흔들리는 것이다. */
  const s0 = session({ expectedWindowCount: 390, windows: { valid: 389 } });
  assert.ok(issues(s0, NO_JOBS).some((i) => i.key === 'ledgerMismatch'), '상세는 이미 낸다');
  const h = sessionHealth(s0, NO_JOBS);
  assert.equal(h.kind, 'caution', '요약이 정상이면 안 된다');
  assert.notEqual(h.kind, 'failure', '셈의 근거 문제지 세션 장애가 아니다');
  assert.match(h.reason, /원장 불일치/);

  /* 마감 세션도 같다 — 종료 분기가 먼저 반환하므로 거기서도 세야 한다 */
  const closed = sessionHealth(
    session({ phase: 'FINALIZED', expectedWindowCount: 390, windows: { valid: 389 } }),
    NO_JOBS,
  );
  assert.equal(closed.tone, 'warn');
  assert.match(closed.reason, /원장 불일치 1/);

  /* 수가 맞으면 종전대로 정상 */
  const ok = sessionHealth(session({ expectedWindowCount: 390, windows: { valid: 390 } }), NO_JOBS);
  assert.equal(ok.kind, 'normal');
  assert.doesNotMatch(ok.reason, /불일치/);
});

test('원장 불일치는 양방향이다 — 행이 더 많아도 셈을 믿을 수 없다', () => {
  /* `issues()` 는 `materialized !== expected` 로 잰다. 요약이 `max(0, expected - materialized)`
   * 로 재면 초과(391 vs 390)를 0으로 접어 요약만 깨끗해진다 — 중복 materialize 든 기대 수
   * 계산 오류든 창 집계를 못 믿는 건 같다. */
  const over = session({ expectedWindowCount: 390, windows: { valid: 391 } });
  assert.ok(issues(over, NO_JOBS).some((i) => i.key === 'ledgerMismatch'), '상세는 이미 낸다');
  assert.equal(sessionHealth(over, NO_JOBS).kind, 'caution', '요약이 정상이면 안 된다');
  assert.match(sessionHealth(over, NO_JOBS).reason, /원장 불일치/);

  const overClosed = sessionHealth(
    session({ phase: 'FINALIZED', expectedWindowCount: 390, windows: { valid: 391 } }),
    NO_JOBS,
  );
  assert.equal(overClosed.tone, 'warn', '마감 세션도 같다');
  assert.match(overClosed.reason, /원장 불일치 1/);
});

test('poll 레인의 어느 조각에도 창·거래 어휘가 남지 않는다 — 덮은 키만 맞고 나머지가 새면 안 된다', () => {
  /* 부분 덮어쓰기 표는 **빠뜨린 키가 곧 기본(가격) 문구로 떨어진다**. 키를 하나씩 세는
   * 단언은 다음에 추가되는 키를 못 잡으므로, 전 조각을 구조로 검사한다. */
  for (const dataset of ['news_minute', 'disclosure_minute']) {
    const segs = segments(
      session({
        dataset,
        expectedWindowCount: 12,
        windows: { valid: 2, validEmpty: 2, incomplete: 2, invalid: 2, missing: 1, overdueNoEvidence: 2, due: 1 },
      }),
    );
    assert.ok(segs.length >= 7, `${dataset}: 조각이 다 서야 이 단언이 의미가 있다`);
    for (const seg of segs) {
      assert.doesNotMatch(seg.label, /창/, `${dataset} ${seg.key}: 라벨에 '창'`);
      assert.doesNotMatch(seg.meaning, /창(?!_end)/, `${dataset} ${seg.key}: 의미에 '창'`);
      assert.doesNotMatch(seg.meaning, /거래/, `${dataset} ${seg.key}: 의미에 '거래'`);
    }
  }
});

test('실을 신호가 없다 = 세션 0 **그리고** 뉴스 job 전 칸 0', () => {
  const zero = { waiting: 0, claimed: 0, claimedExpired: 0, succeeded: 0, dead: 0 };
  assert.equal(hasNoSignal({ sessions: [], newsJobs: zero }), true, '아무것도 없으면 참');
  assert.equal(hasNoSignal({ sessions: [{}], newsJobs: zero }), false, '세션이 있으면 실측이 이긴다');

  /* 🔴 칸마다 따로 재야 한다 — `dead` 만 보던 판이 실제로 있었고, 그때 고착 신호가 검수용
   * 목 뒤로 사라졌다. 어느 칸 하나만 빠뜨려도 그 칸의 장애가 첫 화면에서 조용해진다. */
  for (const k of Object.keys(zero) as (keyof typeof zero)[]) {
    assert.equal(
      hasNoSignal({ sessions: [], newsJobs: { ...zero, [k]: 3 } }),
      false,
      `${k} 가 3인데 "신호 없음"이라 답했다 — 그 칸의 장애가 목에 덮인다`,
    );
  }
});
