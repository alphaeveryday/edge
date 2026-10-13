/* 일별 계열 — 서버가 날짜별로 센 숫자를 그대로 계열로 옮긴다 (ALPHA-1289).
 *
 * 둘을 다룬다: 산출의 기준일별 건수(`/console/facts` 의 `outputs[].history`)와 1분 세션의 일별
 * 창 수(`/sources/minute/daily` 의 `windows`). 판정은 하지 않는다.
 *
 * 지키는 선:
 *   · **서버가 고른 날짜를 그대로 쓴다.** 휴장일·계획 결손일을 표본에서 넣고 빼는 규칙은 서버에
 *     있다. 여기서 날짜를 다시 고르거나 채우지 않는다.
 *   · **산출의 0 은 0 이다.** 결과에 없는 기준일을 서버가 "그날 산출이 없었다는 실측"으로 정했다.
 *   · **조회일 값은 산출 계열에 섞지 않는다.** 조회일이 오늘이면 아직 쌓이는 중인 값이라, 마지막
 *     점으로 서면 매일 급락으로 읽힌다.
 *   · **1분 세션이 없는 날은 `null` 이다 — 0 이 아니다.** 응답에서 데이터셋이 빠진 날이 "그
 *     날짜에 세션이 없었다"는 뜻이다.
 *   · **옛 응답도 받는다.** 새 필드가 없으면 계열이 없다(`null` 또는 전부 `null`).
 *
 * ⚠️ JSX 를 쓰지 않는다 — `node --test` 가 import 할 수 있어야 한다.
 */
import type { DatasetEntry } from './datasetCatalog.ts';
import type { MinuteDailyDay } from './types.ts';

export interface OutputHistory {
  history?: { date: string; count: number }[];
}

/**
 * 산출 하나의 기준일 계열. `history` 가 없는 응답(옛 API)이면 `null` — 그리지 않는다.
 * 빈 목록은 "기준일 표본이 없다"는 서버의 답이라 빈 계열로 돌려준다.
 */
export function outputSeries(output: OutputHistory): { dates: string[]; values: number[] } | null {
  if (!output.history) return null;
  return { dates: output.history.map((d) => d.date), values: output.history.map((d) => d.count) };
}

export interface MinuteDayPoint {
  date: string;
  /** 그날 세션들의 기대 창 수 합 */
  expected: number | null;
  /** 유효 창 = VALID + VALID_EMPTY (빈 데이터도 실행 증거가 있는 창이다) */
  valid: number | null;
  /** 결손 창 = INCOMPLETE + MISSING + INVALID */
  defects: number | null;
}

export interface MinuteDailySeries {
  dataset: DatasetEntry;
  points: MinuteDayPoint[];
}

/**
 * 일별 응답 → 1분 데이터셋별 계열. 행은 `datasets` 순서, 점은 응답의 날짜 순서다.
 *
 * 세션이 없는 날(데이터셋이 그 날짜에 없음)·창 수가 없는 항목(세션 없이 날짜 축 job 만으로 선
 * 항목, 또는 새 필드가 없는 옛 응답)은 세 값이 모두 `null` 이다.
 */
export function minuteDailySeries(days: MinuteDailyDay[], datasets: DatasetEntry[]): MinuteDailySeries[] {
  return datasets.map((dataset) => ({
    dataset,
    points: days.map((day) => {
      const w = day.datasets.find((d) => d.dataset === dataset.sessionDataset)?.windows;
      if (!w) return { date: day.date, expected: null, valid: null, defects: null };
      return {
        date: day.date,
        expected: w.expected,
        valid: w.valid + w.validEmpty,
        defects: w.incomplete + w.missing + w.invalid,
      };
    }),
  }));
}
