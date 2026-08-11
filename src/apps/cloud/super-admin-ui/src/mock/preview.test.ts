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
import { MOCK_ANALYSES, MOCK_GRID, MOCK_MINUTE, MOCK_OVERVIEW, MOCK_REPORT } from './preview.ts';

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
  const wrong = MOCK_REPORT.tasks
    .filter((t) => t.dataset && OPS_DATASET.get(t.taskKey) !== t.dataset)
    .map((t) => `${t.taskKey}: ${t.dataset} ≠ ${OPS_DATASET.get(t.taskKey)}`);
  assert.deepEqual(wrong, [], '리포트 픽스처의 dataset 이 원장과 다르다');
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
