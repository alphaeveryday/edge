/* 데이터셋별 실행 추이 — 격자 응답을 실행 단위 계열로 옮긴다 (ALPHA-1284).
 *
 * 답하는 질문: **이 데이터셋의 실행이 지난 실행들과 달라졌는가**(행 수·소요 시간·종료 상태·
 * 대상 수 대비 받은 수). 판정은 하지 않는다 — 임계는 추이를 본 뒤에 정한다.
 *
 * 지키는 선:
 *   · **점은 하루가 아니라 실행 하나다.** 하루에 여러 번 도는 레인(뉴스·장중 수급)의 슬롯별
 *     행 수가 증분인지 누적인지 확인되지 않았다. 하루로 합치면 뜻이 달라질 수 있다.
 *   · **값이 없는 실행은 `null` 이다 — 0 이 아니다.** 실행 기록 없이 완료로 적힌 작업이 실제로
 *     있고, 그것을 0행·0초로 그리면 거짓이다.
 *   · **옛 응답도 받는다.** UI 와 API 는 따로 배포된다. 새 필드(시각·종료 코드·완전성 숫자)가
 *     없는 응답에서는 그 계열이 전부 `null`·`none` 이다.
 *
 * ⚠️ JSX 를 쓰지 않는다 — `node --test` 가 import 할 수 있어야 한다.
 */
import type { DatasetEntry } from './datasetCatalog.ts';
import type { GridCell, GridSlot } from './types.ts';

/** 0 은 정상, 2 는 부분 성공, 그 밖의 숫자는 실패, 종료 코드가 없으면 기록 없음 */
export type ExitState = 'ok' | 'partial' | 'failed' | 'none';

export const EXIT_LABEL: Record<ExitState, string> = {
  ok: '정상',
  partial: '부분 성공',
  failed: '실패',
  none: '기록 없음',
};

export interface RunPoint {
  runKey: string;
  /** 대표 작업의 records_out */
  rows: number | null;
  /** 이 실행에서 이 데이터셋의 계획된 작업들이 쓴 시간 합(초) */
  durationSec: number | null;
  exit: ExitState;
  /** 완전성 숫자가 있는 작업의 기대 수·받은 수 */
  expected: number | null;
  received: number | null;
}

export interface DatasetRunSeries {
  dataset: DatasetEntry;
  /** 대표 작업 — 응답에 이 데이터셋의 feature 작업이 한 번도 없으면 `null` */
  representative: string | null;
  points: RunPoint[];
}

/**
 * 대표 작업 = `taskKeys` 순서에서 stage 가 `feature` 인 첫 작업.
 *
 * stage 는 카탈로그가 아니라 **원장이 아는 값**이라 밖에서 받는다(화면에서는 격자 셀의 `stage`).
 */
export function representativeTask(
  dataset: DatasetEntry,
  stageOf: (taskKey: string) => string | undefined,
): string | null {
  return dataset.taskKeys.find((key) => stageOf(key) === 'feature') ?? null;
}

export function exitStateOf(code: number | null | undefined): ExitState {
  if (code == null) return 'none';
  if (code === 0) return 'ok';
  return code === 2 ? 'partial' : 'failed';
}

/**
 * 한 실행의 종료 상태 = 작업들 중 가장 나쁜 것. 실패 > 부분 성공 > 기록 없음 > 정상.
 *
 * `기록 없음` 을 `정상` 보다 나쁘게 두는 이유: 세 작업 중 둘이 0 이고 하나가 기록이 없을 때
 * `정상` 이라 하면 없는 기록이 초록 뒤에 숨는다.
 */
function worstExit(cells: GridCell[]): ExitState {
  const states = cells.map((c) => exitStateOf(c.exitCode));
  for (const state of ['failed', 'partial', 'none'] as const) {
    if (states.includes(state)) return state;
  }
  return states.length > 0 ? 'ok' : 'none';
}

/** 작업 하나라도 시각이 없으면 그 실행의 값을 내지 않는다 — 부분 합은 실제보다 짧다 */
function durationOf(cells: GridCell[]): number | null {
  if (cells.length === 0) return null;
  let total = 0;
  for (const cell of cells) {
    if (!cell.startedAt || !cell.finishedAt) return null;
    const ms = Date.parse(cell.finishedAt) - Date.parse(cell.startedAt);
    if (!Number.isFinite(ms) || ms < 0) return null;
    total += ms;
  }
  return total / 1000;
}

/**
 * 격자 응답 → 데이터셋별 실행 계열. 행은 `datasets` 순서, 점은 `slots` 순서(계획 시각 오름차순).
 *
 * 한 실행이 그 데이터셋의 점이 되는 조건은 **그 데이터셋의 작업 셀이 하나라도 있는 것**이다.
 * 작업이 하나도 안 적힌 실행(기동 실패 등)은 실행 키의 레인으로 귀속한다(`dailyRollup` 과 같은
 * 규칙) — 빼면 못 뜬 실행이 추이에서 사라진다. 그 점은 값이 전부 없다.
 * 소요 시간·종료 상태는 계획된(DUE) 작업만 센다 — 휴장으로 건너뛴 작업은 돌 일이 없었다.
 */
export function buildRunSeries(slots: GridSlot[], datasets: DatasetEntry[]): DatasetRunSeries[] {
  const stages = new Map<string, string>();
  for (const slot of slots) for (const cell of slot.tasks) stages.set(cell.taskKey, cell.stage);

  return datasets.map((dataset) => {
    const order = new Map(dataset.taskKeys.map((key, i) => [key, i]));
    const representative = representativeTask(dataset, (key) => stages.get(key));
    const points: RunPoint[] = [];
    for (const slot of slots) {
      const cells = slot.tasks
        .filter((c) => order.has(c.taskKey))
        .sort((a, b) => order.get(a.taskKey)! - order.get(b.taskKey)!);
      const taskless = slot.tasks.length === 0 && slot.runKey.split(':')[0] === dataset.lane;
      if (cells.length === 0 && !taskless) continue;
      const due = cells.filter((c) => c.planStatus === 'DUE');
      const counted = cells.find((c) => c.completenessExpected != null);
      points.push({
        runKey: slot.runKey,
        rows: cells.find((c) => c.taskKey === representative)?.recordsOut ?? null,
        durationSec: durationOf(due),
        exit: worstExit(due),
        expected: counted?.completenessExpected ?? null,
        received: counted?.completenessReceived ?? null,
      });
    }
    return { dataset, representative, points };
  });
}

export function formatDuration(seconds: number | null): string {
  if (seconds === null) return '기록 없음';
  const whole = Math.round(seconds);
  if (whole < 60) return `${whole}초`;
  return `${Math.floor(whole / 60)}분 ${whole % 60}초`;
}
