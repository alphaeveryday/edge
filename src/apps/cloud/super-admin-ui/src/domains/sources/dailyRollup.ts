/* 실행 이력의 일 단위 롤업 — 데이터셋 × 날짜 (ALPHA-738).
 *
 * 격자 셀 하나는 이제 "그 런의 그 작업"이 아니라 **그 데이터셋의 그 날짜 전체**다.
 * 같은 날짜에 런이 여럿이어도(뉴스 15:30 · 시장 15:40) 하나의 박스로 접는다.
 *
 * 지키는 선:
 *   · 기대 실행 수는 주기에서 지어내지 않고 **원장이 실제로 계획한 것**(plan_status=DUE 셀)을
 *     센다. 주기가 다른 데이터셋에 같은 기대치를 적용하지 않게 되는 것은 덤이 아니라 이유다.
 *   · 빈 데이터(VALID_EMPTY — 돌았고 데이터가 없었다는 증거)와 무증거(MISSED — 기한이 지났는데
 *     증거가 없다)는 끝까지 다른 칸에 센다. 합쳐 실패로 만들지 않는다.
 *   · 아직 기한 전인 대기(PENDING)는 실패·누락으로 판정하지 않는다.
 *   · 서버 판정(outcome·dataStatus·running)을 다시 정의하지 않는다 — 세기만 한다.
 */
import type { GridCell, GridSlot } from './types.ts';
import { DATASET_OF_TASK } from './datasetCatalog.ts';

export type DayState = '정상' | '주의' | '장애' | '실행 중' | '계획 스킵' | '계획 없음';

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

export interface DayRun {
  runKey: string;
  taskKey: string;
  outcome: string | null;
  planStatus: string;
  dataStatus: string | null;
  recordsOut: number | null;
  failedRecords: number | null;
  reason: string | null;
  running: boolean;
}

export interface DayRollup {
  datasetId: string;
  date: string;
  state: DayState;
  /** 원장이 그날 이 데이터셋에 계획한 실행 수(plan_status=DUE). 셀이 없으면 0 */
  expected: number;
  counts: DayCounts;
  /** 그 날짜의 실제 실행 목록 — 드릴다운의 재료 */
  runs: DayRun[];
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

/** 슬롯 키에서 날짜만 — "etf-daily:2026-08-03T15:40" → "2026-08-03". tradingDate 가 있으면 그걸 쓴다 */
export function dateOfSlot(slot: GridSlot): string | null {
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
 * 슬롯 전량 → (데이터셋, 날짜) 별 롤업.
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
        r = { datasetId, date, state: '계획 없음', expected: 0, counts: { ...EMPTY }, runs: [] };
        out.set(key, r);
      }
      if (cell.planStatus === 'DUE') r.expected += 1;
      tally(r.counts, cell);
      r.runs.push({
        runKey: slot.runKey,
        taskKey: cell.taskKey,
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
  for (const r of out.values()) r.state = stateOf(r.counts, r.runs.length);
  return out;
}

/** 그룹 행의 상태 — 하위 데이터셋 상태의 결정적 집계 */
const GROUP_ORDER: DayState[] = ['장애', '주의', '실행 중', '정상', '계획 스킵', '계획 없음'];
export function groupState(states: DayState[]): DayState {
  for (const s of GROUP_ORDER) if (states.includes(s)) return s;
  return '계획 없음';
}

/** 격자 창 안의 날짜 축 — 슬롯이 준 날짜만 쓴다(없는 날을 만들지 않는다) */
export function datesOf(slots: GridSlot[]): string[] {
  const set = new Set<string>();
  for (const s of slots) {
    const d = dateOfSlot(s);
    if (d) set.add(d);
  }
  return [...set].sort();
}
