/* 미리보기 픽스처가 **서버가 낼 수 있는 응답**인가 (ALPHA-738).
 *
 * 이 픽스처는 실 데이터가 0건일 때 화면을 검수하는 데 쓴다. 그래서 여기 담긴 조합이
 * 실 API 가 만들 수 없는 모양이면, 검수는 **존재하지 않는 화면**을 승인하거나 존재하는
 * 경로를 한 번도 안 본다. 실제로 두 번 그렇게 뚫렸다:
 *   · 완료 분석에 `publicationStatus` 를 안 채워 소비자(`hasResult`)가 전부 대기로 읽었다.
 *   · 게시 상태·산문 블록을 아무 픽스처도 안 담아 그 경로를 검수가 못 봤다.
 *
 * 실행: node --test src/mock/preview.test.ts
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { groupBySymbol, hasResult } from '../domains/analyses/symbols.ts';
import {
  MOCK_ANALYSES,
  MOCK_GRID,
  MOCK_MINUTE,
  MOCK_OVERVIEW,
  MOCK_REPORT,
  mockReportForRun,
} from './preview.ts';

/** 정본은 파이프라인 소스다(datasetCatalog.test 와 같은 이유 — 두 언어를 잇는 가드가 없다) */
const OPS_SRC = readFileSync(
  new URL('../../../data-pipeline/src/data_pipeline/ops/catalog.py', import.meta.url),
  'utf8',
);
const OPS_TASK_KEYS = new Set([...OPS_SRC.matchAll(/task_key="([^"]+)"/g)].map((m) => m[1]));

/** 작업 → 원장 dataset (`TaskStatus.dataset` 의 축) */
const OPS_DATASET = new Map(
  [...OPS_SRC.matchAll(/task_key="([^"]+)",\s*stage="[^"]*",\s*dataset="([^"]*)"/g)].map((m) => [
    m[1],
    m[2],
  ]),
);

/** 비거래일에 계획이 스킵되는 작업 */
const OPS_CALENDAR_GATED = new Set(
  [...OPS_SRC.matchAll(/task_key="([^"]+)"[\s\S]*?(?=CatalogEntry\(|$)/g)]
    .filter((m) => /kr_trading_calendar=True/.test(m[0]))
    .map((m) => m[1]),
);

test('본문이 있는 분석은 게시 상태를 갖는다 — 서버는 그 조합을 낼 수 없다', () => {
  /* `explanation_result.publication_status` 는 NOT NULL DEFAULT 'DRAFT' 이고 목록 SQL 이
   * 그 테이블만 LEFT JOIN 한다 — 결과 행이 있으면 null 이 나올 수 없다. */
  const impossible = MOCK_ANALYSES.filter(
    (a) =>
      a.publicationStatus == null && // null 과 undefined 를 함께 잡는다 — 둘 다 서버가 못 낸다
      (a.result.trim().length > 0 || (a.resultBlocks?.length ?? 0) > 0),
  );
  assert.deepEqual(
    impossible.map((a) => a.id),
    [],
    '본문은 있는데 게시 상태가 없는 픽스처가 있다',
  );
});

test('소비자가 실제로 유효 설명을 읽는다 — 픽스처가 스스로를 무효로 만들지 않는다', () => {
  const groups = groupBySymbol(MOCK_ANALYSES);
  assert.ok(groups.length > 3, '종목이 몇 개는 있어야 이 단언이 의미가 있다');
  const withValid = groups.filter((g) => g.latestValid !== null);
  assert.ok(
    withValid.length >= groups.length - 1,
    `유효 설명이 있는 종목이 ${withValid.length}/${groups.length} 뿐이다 — 픽스처가 소비자 규칙과 어긋난다`,
  );
});

test('검수가 봐야 할 경로를 픽스처가 실제로 담는다', () => {
  /* 하나도 안 담기면 그 경로는 실 데이터가 0건인 동안 한 번도 안 그려진다 */
  assert.ok(
    MOCK_ANALYSES.some((a) => a.publicationStatus === 'PUBLISHED'),
    '무효화 액션은 PUBLISHED 에서만 활성이다(ALPHA-737)',
  );
  assert.ok(
    MOCK_ANALYSES.some((a) => a.resultBlocks?.some((b) => b.text.trim().length > 0)),
    '고객 산문 블록(ALPHA-878) 경로',
  );
  assert.ok(
    MOCK_ANALYSES.some((a) => a.evidence.some((e) => /^[A-Z_]+$/.test(e.type))),
    '실 API 의 영문 근거 코드 경로',
  );
  assert.ok(
    MOCK_ANALYSES.some((a) => a.evidence.some((e) => !/^[A-Z_]+$/.test(e.type))),
    '코드 전환 전 API 가 보내는 한글 폴백 경로',
  );
  /* 결과가 아직 없는 런도 있어야 한다 — 전부 유효면 대기 표현을 검수할 수 없다 */
  assert.ok(MOCK_ANALYSES.some((a) => !hasResult(a)), '결과 없는 런');
});

test('블록 코드·근거 참조가 엔진이 실제로 저장하는 형식이다', () => {
  /* `statics/interval.py` `final_explanation_payload` — 코드는 H·1·2·3·4|N,
   * 참조는 `bars_5m:<ticker>`·`source_event:<id>`. 형식을 지어내면 상세 화면이 그대로
   * 출력하므로 운영 응답에 없는 모양이 정상 UI 로 승인된다. */
  const withBlocks = MOCK_ANALYSES.filter((a) => a.resultBlocks?.length);
  assert.ok(withBlocks.length > 0, '블록이 없으면 아래 단언은 아무것도 안 잰다');
  for (const a of withBlocks) {
    const codes = a.resultBlocks!.map((b) => b.code);
    /* 엔진은 블록을 **묶음으로** 만든다 — H·1·2·3 은 항상, 마지막은 4(이벤트 있음) 또는
     * N(부재 고지) 하나다. 코드 하나하나가 어휘 안이라는 것만 재면 H·1·4 같은 **엔진이
     * 만들 수 없는 묶음**이 통과한다. 화면이 그 묶음을 그리는 걸 검수하려면 묶음이 맞아야 한다. */
    assert.deepEqual(codes.slice(0, 4), ['H', '1', '2', '3'], `${a.id}: 고정 블록 넷이 순서대로여야 한다`);
    assert.equal(codes.length, 5, `${a.id}: 고정 넷 + 마지막 하나`);
    assert.ok(['4', 'N'].includes(codes[4]), `${a.id}: 마지막은 4 또는 N 이다 (${codes[4]})`);
    assert.equal(new Set(codes).size, codes.length, `${a.id}: 코드가 중복된다`);
    for (const b of a.resultBlocks!) {
      for (const ref of b.evidenceRefs) {
        assert.match(ref, /^(bars_5m|source_event):/, `엔진이 만들지 않는 참조 형식: ${ref}`);
      }
    }
  }
});

test('격자·리포트 픽스처의 작업이 전부 ops 원장에 실재한다 — 없는 경로를 검수가 승인한다', () => {
  /* 실제로 `DISCLOSURE_COLLECTION_DART` 가 남아 있었다 — 공시는 1분 레인으로 옮겨 ops 격자가
   * 그 셀을 더는 못 낸다. 미리보기로 검수하면 존재하지 않는 배치 경로를 정상으로 승인하고,
   * 정작 덮어야 할 disclosure_minute 경로는 한 번도 안 본다. */
  assert.ok(OPS_TASK_KEYS.size > 10, '정본 추출이 실패하면 아래 단언이 아무것도 안 잰다');
  const used = new Set([
    ...MOCK_GRID.slots.flatMap((s) => s.tasks.map((t) => t.taskKey)),
    ...MOCK_REPORT.tasks.map((t) => t.taskKey),
    ...MOCK_OVERVIEW.lanes.flatMap((l) => l.defects.map((d) => d.taskKey)),
  ]);
  const ghost = [...used].filter((k) => !OPS_TASK_KEYS.has(k));
  assert.deepEqual(ghost, [], 'ops 원장에 없는 작업이 미리보기 픽스처에 있다');
});

test('개요가 말한 결함을 드릴다운이 실제로 그릴 수 있다 — 픽스처가 스스로와 모순되지 않는다', () => {
  /* 개요 → 격자·리포트는 같은 `runKey` 로 이어진다. 개요만 결함을 선언하고 그 작업이 격자
   * 슬롯에 없으면, 운영자가 클릭해도 그 행이 없어 UI 경로가 통째로 검수에서 빠진다. */
  for (const lane of MOCK_OVERVIEW.lanes) {
    const slot = MOCK_GRID.slots.find((s) => s.runKey === lane.runKey);
    assert.ok(slot, `개요 레인 ${lane.runKey} 에 대응하는 격자 슬롯이 없다`);
    const inSlot = new Set(slot!.tasks.map((t) => t.taskKey));
    for (const d of lane.defects) {
      assert.ok(inSlot.has(d.taskKey), `${lane.runKey}: 개요가 말한 결함 ${d.taskKey} 를 격자가 못 그린다`);
    }
    /* 귀결 분포까지 같아야 한다 — 같은 런을 두 화면이 다르게 말하면 검수가 어느 쪽도
     * 못 믿는다. 개요만 크게 적으면 드릴다운에서 재현 못 하는 숫자가 된다. */
    const planned = slot!.tasks.filter((t) => t.planStatus !== 'SKIPPED');
    const n = (o: string) => planned.filter((t) => t.outcome === o).length;
    assert.equal(planned.length, lane.counts.due, `${lane.runKey}: due`);
    assert.equal(
      slot!.tasks.length - planned.length,
      lane.counts.skipped,
      `${lane.runKey}: skipped`,
    );
    assert.equal(n('FULFILLED'), lane.counts.fulfilled, `${lane.runKey}: fulfilled`);
    assert.equal(n('FAILED'), lane.counts.failed, `${lane.runKey}: failed`);
    assert.equal(n('MISSED'), lane.counts.missed, `${lane.runKey}: missed`);
    assert.equal(n('BLOCKED'), lane.counts.blocked, `${lane.runKey}: blocked`);
    assert.equal(n('PENDING'), lane.counts.pending, `${lane.runKey}: pending`);

    /* 결함 행의 귀결도 격자와 같아야 한다 — 개요가 BLOCKED 라 한 작업이 격자에선
     * PENDING 이면, 운영자가 들어가서 보는 사실이 개요와 다르다. */
    const outcomeOf = new Map(slot!.tasks.map((t) => [t.taskKey, t.outcome]));
    for (const d of lane.defects) {
      assert.equal(outcomeOf.get(d.taskKey), d.outcome, `${lane.runKey} ${d.taskKey}: 귀결이 갈린다`);
    }
  }
});

test('리포트의 dataset 은 원장 어휘다 — UI 카탈로그의 접기와 다른 값이다', () => {
  /* `TaskStatus.dataset` 은 원장 dataset 이다. `datasetCatalog` 가 산출 테이블을 수집
   * 데이터셋 한 행으로 접는 것과 **다른 축**이라, 접힌 값을 픽스처에 적으면 실
   * `/sources/report` 가 못 내는 라벨을 검수가 승인한다(실제로 `etf_flow`·`investor_flow`·
   * `stock_news` 가 그랬다 — 원장은 각각 investor_flow_load·investor_flow_daily·news_articles). */
  assert.ok(OPS_DATASET.size > 10, '정본 추출 실패');
  /* ⚠️ **null 을 봐주지 않는다.** 원장은 이 작업들에 dataset 을 다 준다 — 픽스처가 비우면
   * 드릴다운이 "—" 를 그리고, 그 라벨 경로가 검수에서 통째로 빠진다. */
  const wrong = MOCK_GRID.slots
    .flatMap((slot) => mockReportForRun(slot.runKey)!.tasks)
    .filter((t) => OPS_DATASET.get(t.taskKey) !== t.dataset)
    .map((t) => `${t.taskKey}: ${t.dataset} ≠ ${OPS_DATASET.get(t.taskKey)}`);
  assert.deepEqual([...new Set(wrong)], [], '리포트 픽스처의 dataset 이 원장과 다르다');
});

test('주말 슬롯은 달력 게이트 작업만 스킵한다 — 레인 전체를 스킵으로 칠하지 않는다', () => {
  /* planner 는 `kr_trading_calendar=True` 인 항목만 비거래일에 스킵한다. 그건 레인이 아니라
   * **작업**마다 다르다 — 뉴스 여섯은 전부 False 라 주말에도 돌고, 시장 레인도 여덟 중
   * 셋만 스킵된다. 전부 스킵으로 칠하면 실 planner 가 못 내는 슬롯이고, "주말엔 아무것도
   * 안 돈다"는 없는 사실을 화면이 배운다. */
  assert.ok(OPS_CALENDAR_GATED.size > 0, '정본 추출 실패');
  const isWeekend = (runKey: string) => /2026-08-0[12]/.test(runKey);
  assert.ok(MOCK_GRID.slots.some((s) => isWeekend(s.runKey)), '주말 슬롯이 있어야 의미가 있다');

  /* ⚠️ 주말 슬롯만 보면 **거래일에 선 스킵**을 놓친다(실제로 거래일 슬롯에
   * `LOAD_ETF_FLOW: SKIPPED / NON_TRADING_DAY_SOURCE` 가 있었다 — 셋 다 불가능한 조합).
   * planner 는 `(not trading) and kr_trading_calendar` 일 때만 SKIPPED 를 쓰고 사유는
   * `states.SKIP_NON_TRADING_DAY` 하나다. 그러니 전 슬롯을 그 술어로 잰다. */
  for (const slot of MOCK_GRID.slots) {
    for (const t of slot.tasks) {
      const skipped = t.planStatus === 'SKIPPED';
      const canSkip = isWeekend(slot.runKey) && OPS_CALENDAR_GATED.has(t.taskKey);
      assert.equal(
        skipped,
        canSkip,
        `${slot.runKey} ${t.taskKey}: 스킵=${skipped} 인데 planner 가 스킵할 수 있나=${canSkip}`,
      );
      if (skipped) {
        assert.equal(t.skipReason, 'NON_TRADING_DAY', `${slot.runKey} ${t.taskKey}: 사유가 어휘 밖`);
      }
    }
  }

  /* 리포트 픽스처도 같은 술어를 따른다 — 격자만 고치고 리포트를 두면 드릴다운에서 되살아난다 */
  for (const t of MOCK_REPORT.tasks) {
    assert.notEqual(
      t.planStatus,
      'SKIPPED',
      `${t.taskKey}: 대표 리포트는 거래일 런이라 계획 스킵이 있을 수 없다`,
    );
  }
});

test('1분 결함 창은 집계 수만큼 목록에도 있다 — 못 들어가는 숫자를 만들지 않는다', () => {
  /* 서버 `GAPS_SQL` 은 LIMIT 없이 결함 창과 무증거 창을 **전부** 낸다. 집계만 크고 목록이
   * 짧으면 운영자가 드릴다운할 수 없는 수가 생기고, 같은 상태가 여럿일 때의 구간 접기가
   * 검수에서 빠진다. */
  for (const s of MOCK_MINUTE.sessions) {
    const n = (st: string) => s.gaps.filter((g) => g.dataStatus === st && !g.noEvidence).length;
    assert.equal(n('INCOMPLETE'), s.windows.incomplete, `${s.dataset}: INCOMPLETE`);
    assert.equal(n('MISSING'), s.windows.missing, `${s.dataset}: MISSING`);
    assert.equal(n('INVALID'), s.windows.invalid, `${s.dataset}: INVALID`);
    assert.equal(
      s.gaps.filter((g) => g.noEvidence).length,
      s.windows.overdueNoEvidence,
      `${s.dataset}: 무증거`,
    );
  }
});

test('개요가 가리키는 런이 곧 그 레인의 최신 런이다 — 서버가 그렇게 고른다', () => {
  /* `OVERVIEW_SQL` 은 `DISTINCT ON (pipeline_type) … ORDER BY pipeline_type, run_key DESC` 다.
   * 더 늦은 run_key 의 런이 격자에 있으면 실 개요는 그걸 고르므로, 픽스처가 앞선 런을
   * 가리키면 실 API 가 낼 수 없는 조합이 된다(재실행 슬롯을 오늘 두면 바로 그렇게 된다). */
  for (const lane of MOCK_OVERVIEW.lanes) {
    const prefix = `${lane.pipelineType}:`;
    const latest = MOCK_GRID.slots
      .map((s) => s.runKey)
      .filter((k) => k.startsWith(prefix))
      .sort()
      .pop();
    assert.equal(lane.runKey, latest, `${lane.pipelineType}: 개요가 최신 런을 안 가리킨다`);
  }
});

/** 실행 전체가 terminal 실패로 끝난 상태 — `SourceService.ORCHESTRATION_TERMINAL_FAILED` */
const TERMINAL_FAILED = new Set(['FAILED', 'TIMED_OUT', 'ABORTED']);

test('개요 배지는 서버 파생 규칙이 낼 수 있는 값이다 — 하드코딩이 규칙을 앞지르지 않는다', () => {
  /* `SourceService.opsStatus` 를 그대로 옮긴 것이다. 픽스처가 규칙 밖 값을 들면(예: 기동은
   * 됐는데 BLOCKED, RUNNING 인데 DEGRADED) 실 `/sources/overview` 가 못 내는 배지를
   * 검수가 승인한다. 규칙이 바뀌면 이 테스트가 먼저 깨지는 게 맞다. */
  for (const lane of MOCK_OVERVIEW.lanes) {
    const launchFailed =
      lane.launchStatus === 'LAUNCH_FAILED' || lane.launchStatus === 'LAUNCH_CONFLICT';
    const runTerminalFailed = TERMINAL_FAILED.has(lane.orchestrationStatus ?? '');
    const expected = launchFailed
      ? 'BLOCKED'
      : !runTerminalFailed && lane.orchestrationStatus === 'RUNNING'
        ? 'IN_PROGRESS'
        : lane.orchestrationStatus == null || lane.orchestrationStatus === 'UNKNOWN'
          ? 'UNKNOWN'
          : lane.defects.length === 0 && !runTerminalFailed
            ? 'READY'
            : 'DEGRADED';
    assert.equal(lane.opsStatus, expected, `${lane.runKey}: 배지가 서버 규칙과 다르다`);
  }
});

test('마감 경과(overdue)는 미귀결에만 붙는다 — 귀결된 결함에 붙이면 없는 사유가 선다', () => {
  /* `SourceService.overdue` 는 `pendingOutcome`(outcome null 또는 PENDING)일 때만 참이다.
   * BLOCKED·MISSED 에 붙이면 "선행 미충족 · 마감 경과 미귀결" 같은 실 API 가 못 내는
   * 사유 조합이 화면에 서고, 그 문구 경로가 검수를 통과한다. */
  for (const lane of MOCK_OVERVIEW.lanes) {
    for (const d of lane.defects) {
      if (!d.overdue) continue;
      assert.ok(
        d.outcome == null || d.outcome === 'PENDING',
        `${lane.runKey} ${d.taskKey}: ${d.outcome} 인데 overdue 다`,
      );
    }
  }
});

/** 레인별 ops 작업 전량 — planner 는 `catalog.entries(pipeline_type)` 를 통째로 계획한다 */
const OPS_BY_LANE = (() => {
  const out = new Map<string, string[]>();
  for (const m of OPS_SRC.matchAll(/CatalogEntry\(([\s\S]*?)\n    \)/g)) {
    const body = m[1];
    const key = /task_key="([^"]+)"/.exec(body)?.[1];
    if (!key) continue;
    const lane = /pipeline_type="([^"]+)"/.exec(body)?.[1] ?? 'etf-daily';
    out.set(lane, [...(out.get(lane) ?? []), key]);
  }
  return out;
})();

test('격자 슬롯은 그 레인의 ops 작업을 전부 담는다 — 일부만 담은 런은 서버가 못 낸다', () => {
  /* planner 가 레인의 카탈로그 항목을 통째로 계획하므로, 8개만 담은 etf-daily 런은
   * 실 `/sources/grid`·`/sources/overview` 가 낼 수 없다. 개요 due 가 그 수에 매이고,
   * 빠진 데이터셋의 행과 드릴다운이 검수에서 통째로 사라진다. */
  assert.ok((OPS_BY_LANE.get('etf-daily') ?? []).length > 10, '레인 추출 실패');
  for (const [lane, expected] of OPS_BY_LANE) {
    const slots = MOCK_GRID.slots.filter((s) => s.runKey.startsWith(`${lane}:`) && s.tasks.length > 0);
    if (slots.length === 0) continue; // 픽스처가 안 담은 레인은 아래 별도 단언이 드러낸다
    for (const slot of slots) {
      if (slot.tasks.length === 1) continue; // 재실행 슬롯 — 실패분만 다시 돈다
      assert.deepEqual(
        slot.tasks.map((t) => t.taskKey).sort(),
        [...expected].sort(),
        `${slot.runKey}: 레인 작업 전량이 아니다`,
      );
    }
  }
});

test('픽스처가 안 담은 레인을 드러낸다 — 조용히 빠지면 그 화면이 없는 줄 모른다', () => {
  /* ops 에는 레인이 셋(etf-daily·news·investor-intraday)인데 격자 픽스처는 둘만 담는다.
   * 불가능한 조합은 아니지만(그 레인 런이 없던 날일 수 있다) **검수 공백**이다 —
   * 여기서 이름을 불러 두면 다음 사람이 "없다"를 "안 만든다"로 읽지 않는다. */
  const inFixture = new Set(MOCK_GRID.slots.map((s) => s.runKey.split(':')[0]));
  const missing = [...OPS_BY_LANE.keys()].filter((l) => !inFixture.has(l));
  assert.deepEqual(missing, ['investor-intraday'], '안 담은 레인 목록이 바뀌었다 — 의도인지 확인하라');
});

/** 작업 → 선행 작업 — 정본은 `ops/catalog.py` 의 `depends_on` */
const OPS_DEPENDS = new Map(
  [...OPS_SRC.matchAll(/CatalogEntry\(([\s\S]*?)\n    \)/g)]
    .map((m) => {
      const key = /task_key="([^"]+)"/.exec(m[1])?.[1];
      const raw = /depends_on=\(([^)]*)\)/.exec(m[1])?.[1] ?? '';
      return [key, [...raw.matchAll(/"([^"]+)"/g)].map((d) => d[1])] as const;
    })
    .filter(([k, v]) => k && v.length > 0) as [string, string[]][],
);

test('닫힌 게이트 뒤에 성공한 작업이 없다 — 선행 미충족은 진입 자체를 못 한다', () => {
  /* wrapper 는 `depends_on` 미충족을 BLOCKED 로 적는다. 픽스처가 그 뒤를 FULFILLED·PENDING·
   * MISSED 로 두면 실 원장에 설 수 없는 런이 되고, 검수는 "닫힌 게이트 뒤에서 성공한 적재"를
   * 정상 화면으로 승인한다. 연쇄를 손으로 적으면 목록이 늘 때마다 다시 세야 하므로
   * 그래프에서 파생하고, 그 파생이 실제로 맞는지 여기서 잰다. */
  assert.ok(OPS_DEPENDS.size > 5, '정본 추출 실패');
  for (const slot of MOCK_GRID.slots) {
    if (slot.tasks.length <= 1) continue; // 재실행 슬롯
    const outcomeOf = new Map(slot.tasks.map((t) => [t.taskKey, t]));
    for (const t of slot.tasks) {
      const deps = OPS_DEPENDS.get(t.taskKey);
      if (!deps || t.planStatus === 'SKIPPED' || t.outcome === 'BLOCKED') continue;
      for (const d of deps) {
        const up = outcomeOf.get(d);
        if (!up) continue;
        assert.equal(
          up.planStatus === 'SKIPPED' || up.outcome === 'FULFILLED',
          true,
          `${slot.runKey} ${t.taskKey}(${t.outcome}): 선행 ${d} 가 ${up.outcome} 인데 진입했다`,
        );
      }
    }
  }
});

test('픽스처의 선행 선언이 ops 정본과 같다 — 여기서 갈리면 위 연쇄가 거짓을 만든다', () => {
  /* 연쇄를 픽스처의 `dependsOn` 에서 파생하므로, 그 선언이 낡으면 **틀린 연쇄가 조용히
   * 정답처럼** 굳는다. 선언 자체를 정본과 맞물린다. */
  const declared = new Map(
    [...MOCK_GRID.slots.flatMap((s) => s.tasks)].map((t) => [t.taskKey, t] as const),
  );
  for (const [key, deps] of OPS_DEPENDS) {
    if (!declared.has(key)) continue;
    assert.ok(deps.length > 0, `${key}: 정본 추출이 비었다`);
  }
  /* 픽스처가 담은 작업 중 정본에 선행이 있는데 픽스처 목록엔 없는 것 — 위 단언이 아무것도
   * 안 재게 되는 형태라 따로 잡는다. */
  const covered = [...OPS_DEPENDS.keys()].filter((k) => declared.has(k));
  assert.ok(covered.length >= 10, `선행 있는 작업이 ${covered.length}개만 담겼다`);
});

test('리포트는 그 런의 작업을 전부 낸다 — 격자에서 보이는 칸을 눌렀는데 행이 없으면 안 된다', () => {
  /* 실 `/sources/report` 는 그 런의 `ops_expected_task` 를 전부 낸다(`TASKS_SQL`).
   * 손으로 쓴 상세만 담으면 새로 채운 칸의 드릴다운이 통째로 빈다 — 그래서 목록은 격자
   * 슬롯에서 파생하고 상세는 그 위에 얹는다. 이 단언이 그 파생을 고정한다. */
  for (const slot of MOCK_GRID.slots) {
    const report = mockReportForRun(slot.runKey);
    assert.ok(report, `${slot.runKey}: 리포트가 없다`);
    assert.deepEqual(
      report!.tasks.map((t) => t.taskKey).sort(),
      slot.tasks.map((t) => t.taskKey).sort(),
      `${slot.runKey}: 리포트와 격자의 작업이 다르다`,
    );
  }

  /* 상태 축은 셀에서만 온다 — 상세 표가 상태를 아예 안 들고 있어 드리프트의 원천이 없다.
   * (전에는 상세가 행을 통째로 갈아, 연쇄로 BLOCKED 가 된 작업이 리포트에선 성공으로
   * 남았다. 그때 "귀결이 같다"는 단언을 달았지만 병합을 고친 뒤로는 **구조상 깨질 수 없어**
   * 아무것도 재지 못했다 — 단언 대신 원천을 없앴다.) */
  const slot = MOCK_GRID.slots.find((x) => x.runKey === MOCK_REPORT.run?.runKey)!;
  const gridOutcome = new Map(slot.tasks.map((t) => [t.taskKey, t.outcome]));
  for (const t of MOCK_REPORT.tasks) {
    assert.equal(t.outcome, gridOutcome.get(t.taskKey), `${t.taskKey}: 리포트 귀결이 격자와 다르다`);
  }

  /* 상세가 살아 있는지 — 파생으로 바꾸면서 손으로 쓴 재시도·완전성이 날아가기 쉽다 */
  const rich = MOCK_REPORT.tasks.find((t) => t.taskKey === 'PRICE_COLLECTION_KIS')!;
  assert.ok(rich.attempts.length > 1, '재시도 상세가 남아야 한다');
  assert.ok(
    MOCK_REPORT.tasks.some((t) => t.completeness !== null),
    '완전성 대조가 남아야 한다',
  );
});

/** 서버 `TaskStatus.currentAttempt()` — RUNNING 이 있으면 그중 마지막, 없으면 마지막 */
const currentAttemptOf = (attempts: { executionStatus: string | null; finishedAt: string | null }[]) =>
  attempts.filter((a) => a.executionStatus === 'RUNNING').at(-1) ?? attempts.at(-1) ?? null;

test('리포트 헤더는 자기 시도 목록과 같은 말을 한다 — 서버는 둘 다 현재 시도에서 뽑는다', () => {
  /* `SourceReportResponse.TaskResponse.from` 은 `executionStatus`·`lastFinishedAt` 을
   * `currentAttempt()` 에서 파생한다. 픽스처가 시도 목록만 갈아끼우면 "15:40 에 타임아웃"
   * 헤더 아래 16:02 FAILED 시도가 붙어, 검수가 자기모순인 상세를 정상으로 승인한다. */
  for (const slot of MOCK_GRID.slots) {
    for (const t of mockReportForRun(slot.runKey)!.tasks) {
      const cur = currentAttemptOf(t.attempts);
      assert.equal(t.executionStatus, cur?.executionStatus ?? null, `${slot.runKey} ${t.taskKey}: 실행 상태`);
      assert.equal(t.lastFinishedAt, cur?.finishedAt ?? null, `${slot.runKey} ${t.taskKey}: 종료 시각`);
    }
  }
});

test('손으로 쓴 시도 이력이 격자 귀결과 같은 결말이다 — 두 벌이 갈리면 상세가 거짓이 된다', () => {
  /* 위 단언은 파생을 고정할 뿐, **상세 표 자체가 낡는 것**은 못 잡는다(헤더가 상세를 따라
   * 같이 틀려질 뿐이다). 시도의 결말과 셀의 귀결이 같은지는 따로 재야 한다. */
  const slot = MOCK_GRID.slots.find((x) => x.runKey === MOCK_REPORT.run?.runKey)!;
  const outcomeOf = new Map(slot.tasks.map((t) => [t.taskKey, t.outcome]));
  const ENDING: Record<string, string> = { SUCCEEDED: 'FULFILLED', FAILED: 'FAILED', TIMED_OUT: 'FAILED', RUNNING: 'PENDING' };
  for (const t of MOCK_REPORT.tasks) {
    const cur = currentAttemptOf(t.attempts);
    if (!cur?.executionStatus) continue;
    assert.equal(
      ENDING[cur.executionStatus],
      outcomeOf.get(t.taskKey),
      `${t.taskKey}: 시도는 ${cur.executionStatus} 인데 격자 귀결은 ${outcomeOf.get(t.taskKey)}`,
    );
  }
});

test('개요가 센 실패는 전부 누를 수 있는 행으로 있다 — 숫자만 있고 행이 없으면 안 된다', () => {
  /* `SourceService.toLane()` 은 결함 목록과 counts 를 같은 원장에서 만든다. 목록만 손으로
   * 적으면 counts 는 연쇄를 반영해 늘어나는데 행은 옛것으로 남아, "결함 8" 이라 말하면서
   * 누를 행은 셋뿐인 상태가 된다. 귀결 실패는 전부 행이 있어야 한다. */
  for (const lane of MOCK_OVERVIEW.lanes) {
    const outcomeFailures = lane.counts.failed + lane.counts.missed + lane.counts.blocked;
    assert.ok(
      lane.defects.length >= outcomeFailures,
      `${lane.runKey}: 실패 ${outcomeFailures} 인데 결함 행은 ${lane.defects.length}`,
    );
    const listed = new Set(lane.defects.map((d) => d.taskKey));
    const slot = MOCK_GRID.slots.find((x) => x.runKey === lane.runKey)!;
    for (const t of slot.tasks) {
      if (t.planStatus === 'SKIPPED') continue;
      if (t.outcome === 'FAILED' || t.outcome === 'MISSED' || t.outcome === 'BLOCKED') {
        assert.ok(listed.has(t.taskKey), `${lane.runKey} ${t.taskKey}: 실패인데 결함 목록에 없다`);
      }
    }
  }
});
