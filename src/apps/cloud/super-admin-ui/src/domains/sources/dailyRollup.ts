/* 실행 이력의 일 단위 롤업 — 데이터셋 × 날짜 × **실행 인스턴스** (ALPHA-738).
 *
 * 격자 셀 하나는 "그 데이터셋의 그 날짜 전체"다. 그 아래 계층은 **실행**이고, 작업은 실행의
 * 하위다:
 *
 *   데이터셋 × 날짜 → 실행 인스턴스 → 작업
 *
 * ⚠️ **작업을 실행으로 세지 않는다.** 한 런(runKey)에 수집·정제·적재 작업이 3개 있어도 실행은
 * 1회다. 예전에는 DUE 셀마다 기대 실행을 +1 해서 "실행 2회"가 "기대 실행 4"로 보였다 —
 * 실행 수와 작업 수가 같은 축이 되면 하루 10회 실행이 30행으로 평탄화된다.
 *
 * 실행 인스턴스 키는 `datasetId × date × runKey` 다. 같은 거래일에 정규·수동·백필·재실행이
 * 함께 있으므로 날짜로 묶지 않는다(RunAxisPage 와 같은 이유).
 *
 * 지키는 선:
 *   · 기대 실행 수는 주기에서 지어내지 않고 **원장이 실제로 계획한 것**(plan_status=DUE 가
 *     하나라도 있는 런)을 센다.
 *   · 빈 데이터(VALID_EMPTY — 돌았고 데이터가 없었다는 증거)와 무증거(MISSED — 기한이 지났는데
 *     증거가 없다)는 끝까지 다른 칸에 센다. 합쳐 실패로 만들지 않는다.
 *   · 아직 기한 전인 대기(PENDING)는 실패·누락으로 판정하지 않는다.
 *   · 서버 판정(outcome·dataStatus·running)을 다시 정의하지 않는다 — 세기만 한다.
 */
import type { GridCell, GridSlot } from './types.ts';
import { DATASET_OF_TASK } from './datasetCatalog.ts';

/**
 * 하루·실행 하나의 상태.
 *
 * `상태 미제공` 은 판정이 아니라 **API 가 그 날짜의 판정 값을 주지 않았다**는 사실이다 —
 * 데이터 출처가 다르다는 이유로 운영 상태를 만들지 않기 위해 둔다(실시간 데이터셋은 최근
 * 7일 요약 엔드포인트가 없다). `계획 없음`(계획 행이 없다)과 다른 사실이라 합치지 않는다.
 */
export type DayState =
  | '정상'
  | '주의'
  | '장애'
  | '실행 중'
  | '계획 스킵'
  | '계획 없음'
  | '상태 미제공';

export interface DayCounts {
  /** 기한이 지난 기대 실행 중 정상 귀결 */
  fulfilled: number;
  /** 돌았고 그 분·그 날 데이터가 없었다는 **증거가 남은** 것 — 실패가 아니다 */
  emptyEvidence: number;
  failed: number;
  incomplete: number;
  invalid: number;
  /** 기한이 지났는데 실행·결과 증거가 없다 */
  noEvidence: number;
  /** 아직 판정 기한 전 — 실패가 아니다 */
  pending: number;
  running: number;
  skipped: number;
  /** 스텝이 스스로 판정한 유실 합계(잡별 단위 상이) */
  failedRecords: number;
}

/** 실행 하나 안의 작업. 실행을 펼쳤을 때만 보이는 하위 층이다. */
export interface DayTask {
  taskKey: string;
  stage: string;
  outcome: string | null;
  planStatus: string;
  dataStatus: string | null;
  recordsOut: number | null;
  failedRecords: number | null;
  reason: string | null;
  running: boolean;
}

/**
 * 실행 인스턴스 하나 — 격자 상세의 기본 행이다.
 * `planned` 는 이 실행에 계획(DUE) 작업이 하나라도 있었나다(기대 실행 수의 분자).
 */
export interface DayExecution {
  runKey: string;
  state: DayState;
  planned: boolean;
  counts: DayCounts;
  tasks: DayTask[];
}

export interface DayRollup {
  datasetId: string;
  date: string;
  state: DayState;
  /** 원장이 그날 이 데이터셋에 계획한 **실행 수**(작업 수가 아니다) */
  expected: number;
  /** 작업 축 합계 — 실행 축과 다른 층이라 이름으로 구분해 둔다 */
  counts: DayCounts;
  /** 그 날짜의 실행 인스턴스 목록 — 드릴다운의 재료 */
  executions: DayExecution[];
}

const EMPTY: DayCounts = {
  fulfilled: 0,
  emptyEvidence: 0,
  failed: 0,
  incomplete: 0,
  invalid: 0,
  noEvidence: 0,
  pending: 0,
  running: 0,
  skipped: 0,
  failedRecords: 0,
};

/**
 * 이 런이 어느 날에 속하는가 — tradingDate 가 있으면 그것, 없으면 슬롯 키의 날짜
 * ("etf-daily:2026-08-03T15:40" → "2026-08-03").
 *
 * ⚠️ **격자의 열 축이자 `/console/facts?date=` 의 조회 축이다.** 서버의 창은
 * `COALESCE(trading_date, created_at 의 KST 날짜)` 다(`JdbcConsoleFactsRepository.RUN_DAY`).
 * 여기서 고른 날을 실행 상세로 넘기므로 두 식이 갈리면 링크가 창 밖으로 나간다 —
 * **언제 갈리는지가 정확히 아래 하나다**:
 *   · `tradingDate` 가 있으면 서버도 그 값을 쓴다(COALESCE 의 첫 항) → **항상 일치**.
 *     Planner 는 슬롯 시각의 KST 날짜를 `trading_date` 에 그대로 넣으므로(`ops/planner.py` 의
 *     `day = slot.date()` · `run_key = slot_run_key(slot, …)`) 정규 런은 여기 들어온다.
 *   · `tradingDate` 가 **null** 이면 여기는 런 키의 슬롯 날짜를, 서버는 `created_at` 의 KST
 *     날짜를 쓴다 → **슬롯을 만든 날과 도는 날이 다르면 갈린다**(과거 슬롯을 나중에 세운
 *     백필·레거시 런). 그때 목적지는 "이 조회 결과에 그 실행이 없습니다"로 그 사실을 밝힌다 —
 *     없다고 말하지 않는다(리뷰 2라운드가 이 경로를 짚었다).
 *   · **런 행이 아예 없는 계획 슬롯**(`PLANNER_MISSING`)은 `RUN_DAY` 를 안 탄다 —
 *     `MISSING_SLOTS_SQL` 이 슬롯 키의 날짜를 문자열로 맞추고 `missingSlot()` 이 그 키에서
 *     `trading_date` 를 합성한다. 결과는 첫 항목과 같지만 **경로가 다르다**: 이 축을 고칠 때
 *     `RUN_DAY` 만 보면 계획 결손 슬롯이 따로 깨진다(리뷰 3라운드).
 * 슬롯 날짜를 버리고 `created_at` 을 따라갈 수는 없다 — 이 함수의 다른 소비자인 격자의 **열**
 * 은 "그 데이터셋의 그 날"이라 슬롯 날짜가 맞고, 생성일로 옮기면 백필 런이 실행한 날의 칸에
 * 서서 그날 실제로 돈 런과 섞인다.
 *
 * 인자를 `GridSlot` 이 아니라 구조로 받는 이유: 실행 중인 배치(`OverviewLane`)도 같은 축을
 * 물어야 하는데, 날짜 규칙이 화면마다 갈리면 같은 런이 화면에 따라 다른 날짜로 조회된다.
 *
 * ⚠️ **`tradingDate` 는 옵셔널이 아니다 — 일부러 필수다.** 옵셔널이면 거래일을 쥔 호출부가
 * `dateOfSlot({ runKey: l.runKey })` 로 **컴파일되게** 축을 흘릴 수 있고, 그러면 우선순위가
 * 호출부에서 조용히 뒤집힌다(단위 테스트는 헬퍼만 보고, 소스 가드는 `date` 키만 보고, 하네스
 * 픽스처는 두 날짜가 같아 전부 통과한다 — 리뷰 5라운드가 이 경로를 실증했다). 필수로 두면
 * tsc 가 잡는다. 거래일을 **모르는** 자리(구성종목 결손: `runKey` 가 URL 로만 온다)는
 * `{ tradingDate: null, runKey }` 를 **명시해서** 넘긴다 — 축을 못 보는 것이 선언이 되고,
 * 거래일을 쥔 화면이 같은 걸 쓰면 눈에 띈다.
 * ⛔ 폴백 갈래를 `dateOfRunKey` 같은 **별도 export 로 빼지 마라**(6라운드에 그렇게 했다가
 * 되돌렸다): 그러면 그 이름을 부르는 것만으로 위 tsc 가드를 통과해 우선순위를 우회할 수 있고,
 * 같은 규칙이 두 곳에 서서 갈릴 자리가 생긴다.
 *
 * ⚠️ **빈 문자열은 부재로 접는다**(`??` 가 아니라 조건식인 이유). 와이어 타입은
 * `string | null` 이라 `''` 는 날짜도 아니고 선언된 부재도 아닌 **불량값**이다. `??` 로 쓰면
 * `''` 가 그대로 나가 `runHref` 의 falsy 필터에서 사라지고, 링크가 조용히 "날짜 없음"으로
 * 퇴행한다 — 가드와 바인딩이 falsy 를 다르게 읽던 그 자리다.
 *
 * ❌ **그렇다고 `''` 에 `null` 을 돌려주지 않는다**(리뷰 7라운드 제기 — Rule 12 로 "불량값을
 * 드러내라"고 봤다). 그 처방이 여기서는 **더 나쁜 쪽으로 숨긴다**: 이 함수의 다른 소비자인
 * `rollup`·`datesOf` 는 날짜가 없는 슬롯을 **통째로 건너뛴다**(`if (!date) continue`). 즉
 * 불량값 하나가 실행 이력에서 **그 런을 사라지게** 만든다 — 잘못된 열에 서는 것보다 나쁘다
 * (없는 것과 못 읽은 것이 같은 모양이 된다). 그리고 오늘 이 값은 서버가 만들 수 없다:
 * `trading_date` 는 `LocalDate` 로 매핑돼 날짜 아니면 `null` 이다.
 * ⚠️ **드러낼 자리를 `parseFacts` 라고 적었던 것은 틀렸다**(리뷰 8라운드가 잡았다) — 그건
 * `/console/facts` 만 검사하고, 이 함수의 입력은 `/sources/grid`(`GridSlot`)와
 * `/sources/overview`(`OverviewLane`)에서 **타입 캐스팅으로 그냥 들어온다**. 즉 두 응답에는
 * 검증 경계가 **아예 없다**. 언젠가 fail-loud 가 필요해지면 순수 함수인 여기가 아니라 그
 * 두 응답에 경계를 **새로 만들어야** 한다 — 지금 그 처방은 코드에 존재하지 않는다.
 */
export function dateOfSlot(slot: { tradingDate: string | null; runKey: string }): string | null {
  if (slot.tradingDate) return slot.tradingDate;
  return slot.runKey.match(/(\d{4}-\d{2}-\d{2})/)?.[1] ?? null;
}

function tally(counts: DayCounts, cell: GridCell) {
  if (cell.planStatus === 'SKIPPED') {
    counts.skipped += 1;
    return;
  }
  counts.failedRecords += cell.failedRecords ?? 0;
  if (cell.dataStatus === 'VALID_EMPTY') counts.emptyEvidence += 1;
  if (cell.dataStatus === 'INCOMPLETE') counts.incomplete += 1;
  if (cell.dataStatus === 'INVALID') counts.invalid += 1;

  switch (cell.outcome) {
    case 'FULFILLED':
      counts.fulfilled += 1;
      return;
    case 'FAILED':
      counts.failed += 1;
      return;
    case 'MISSED':
      counts.noEvidence += 1;
      return;
    case 'BLOCKED':
      /* 선행이 안 돼 진입 못 함 — 이 데이터셋이 그날 귀결되지 못한 것이므로 장애 축에 둔다 */
      counts.failed += 1;
      return;
    default:
      /* PENDING 또는 모르는 값. 도는 시도가 있으면 실행 중, 없으면 아직 기한 전 대기다 */
      if (cell.running) counts.running += 1;
      else counts.pending += 1;
  }
}

/** 결정적 상태 판정 — 순서가 곧 규칙이다. 새 점수를 만들지 않는다. */
export function stateOf(counts: DayCounts, cellCount: number): DayState {
  if (cellCount === 0) return '계획 없음';
  if (counts.skipped === cellCount) return '계획 스킵';
  if (counts.failed > 0 || counts.noEvidence > 0) return '장애';
  if (counts.incomplete > 0 || counts.invalid > 0 || counts.failedRecords > 0) return '주의';
  /* 아직 끝나지 않은 것이 남아 있다 — 대기를 실패로 보지 않는다 */
  if (counts.running > 0 || counts.pending > 0) return '실행 중';
  return '정상';
}

/**
 * 하루의 상태 — **실행 상태의 결정적 집계**다. 실행이 실제 제어 단위라서 그 층에서 접는다.
 * (예전 그룹 롤업과 함수 모양은 같지만 대상이 다르다: 시장·뉴스 같은 분류 층이 아니라
 * 원장에 실재하는 실행 인스턴스다.)
 */
const DAY_ORDER: DayState[] = [
  '장애',
  '주의',
  '실행 중',
  '정상',
  '계획 스킵',
  '상태 미제공',
  '계획 없음',
];
export function dayStateOf(states: DayState[]): DayState {
  for (const s of DAY_ORDER) if (states.includes(s)) return s;
  return '계획 없음';
}

/**
 * 슬롯 전량 → (데이터셋, 날짜) 별 롤업. 실행 인스턴스는 `runKey` 로 묶는다.
 * 카탈로그에 없는 작업은 어느 데이터셋에도 넣지 않는다 — 임의 배정 금지.
 */
export function rollup(slots: GridSlot[]): Map<string, DayRollup> {
  const out = new Map<string, DayRollup>();
  for (const slot of slots) {
    const date = dateOfSlot(slot);
    if (!date) continue;
    for (const cell of slot.tasks) {
      const datasetId = DATASET_OF_TASK[cell.taskKey];
      if (!datasetId) continue;
      const key = `${datasetId}|${date}`;
      let r = out.get(key);
      if (!r) {
        r = { datasetId, date, state: '계획 없음', expected: 0, counts: { ...EMPTY }, executions: [] };
        out.set(key, r);
      }
      /* 같은 runKey 의 작업은 한 실행에 모은다 — 여기가 "작업 3개 = 실행 3회" 를 막는 지점이다 */
      let exec = r.executions.find((e) => e.runKey === slot.runKey);
      if (!exec) {
        exec = { runKey: slot.runKey, state: '계획 없음', planned: false, counts: { ...EMPTY }, tasks: [] };
        r.executions.push(exec);
      }
      if (cell.planStatus === 'DUE') exec.planned = true;
      tally(exec.counts, cell);
      tally(r.counts, cell);
      exec.tasks.push({
        taskKey: cell.taskKey,
        stage: cell.stage,
        outcome: cell.outcome,
        planStatus: cell.planStatus,
        dataStatus: cell.dataStatus,
        recordsOut: cell.recordsOut,
        failedRecords: cell.failedRecords,
        reason: cell.skipReason ?? cell.outcomeReason,
        running: cell.running,
      });
    }
  }
  for (const r of out.values()) {
    for (const e of r.executions) e.state = stateOf(e.counts, e.tasks.length);
    /* 기대 실행 = 계획(DUE)이 있던 실행 인스턴스 수. 작업 수가 아니다. */
    r.expected = r.executions.filter((e) => e.planned).length;
    r.state = dayStateOf(r.executions.map((e) => e.state));
  }
  return out;
}

/* 그룹 롤업(groupState)은 제거했다 — 시장·뉴스·장중은 실제 제어 단위가 아니라서 그 층위에
 * 성공/주의/장애를 매기면 원장에 근거가 없는 상태가 생긴다. 그 축은 필터·배지로만 쓴다. */

/** 격자 창 안의 날짜 축 — 슬롯이 준 날짜만 쓴다(없는 날을 만들지 않는다) */
export function datesOf(slots: GridSlot[]): string[] {
  const set = new Set<string>();
  for (const s of slots) {
    const d = dateOfSlot(s);
    if (d) set.add(d);
  }
  return [...set].sort();
}
