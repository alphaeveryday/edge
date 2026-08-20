import type { IntradayAnalysisTrendDto, IntradayAnalysisTrendPointDto } from './types.ts';

const object = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);
const count = (value: unknown): value is number =>
  typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
const validDate = (value: unknown): value is string => {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(parsed.valueOf()) && parsed.toISOString().slice(0, 10) === value;
};

function parsePoint(value: unknown, index: number): IntradayAnalysisTrendPointDto {
  if (!object(value)) throw new Error(`장중 분석 귀결 points[${index}]가 객체가 아닙니다.`);
  const keys = ['triggers', 'observations', 'runs', 'activeRuns', 'failedRuns', 'results', 'published'] as const;
  if (!validDate(value.date) || keys.some((key) => !count(value[key]))) {
    throw new Error(`장중 분석 귀결 points[${index}]의 날짜·계수가 유효하지 않습니다.`);
  }
  const point = value as unknown as IntradayAnalysisTrendPointDto;
  if (
    point.triggers < point.observations ||
    point.observations < point.runs ||
    point.runs < point.results ||
    point.results < point.published ||
    point.activeRuns + point.failedRuns > point.runs
  ) {
    throw new Error(`장중 분석 귀결 points[${index}]의 단계 불변식이 깨졌습니다.`);
  }
  return { ...point };
}

/** 손상된 응답을 0건이나 정상 판정으로 흘리지 않고 query 실패로 격리한다. */
export function parseIntradayAnalysisTrend(
  value: unknown,
  maxDate?: string,
  days = 30,
): IntradayAnalysisTrendDto {
  if (
    !object(value) ||
    typeof value.asOf !== 'string' ||
    !/^\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d(?:\.\d{1,9})?)?(?:Z|[+-](?:(?:0\d|1[0-7]):[0-5]\d|18:00))$/.test(value.asOf) ||
    !validDate(value.asOf.slice(0, 10)) ||
    Number.isNaN(Date.parse(value.asOf))
  ) {
    throw new Error('장중 분석 귀결 응답의 asOf가 유효하지 않습니다.');
  }
  if (!Array.isArray(value.points) || value.points.length !== days) {
    throw new Error('장중 분석 귀결 응답이 요청한 날짜 수와 일치하지 않습니다.');
  }
  const points = value.points.map(parsePoint);
  for (let i = 0; i < points.length; i += 1) {
    if (i > 0) {
      const expected = new Date(`${points[i - 1].date}T00:00:00Z`);
      expected.setUTCDate(expected.getUTCDate() + 1);
      if (expected.toISOString().slice(0, 10) !== points[i].date) {
        throw new Error('장중 분석 귀결 points가 연속 날짜 오름차순이 아닙니다.');
      }
    }
  }
  const actualMaxDate = points.at(-1)?.date;
  const asOfDate = kstDateAt(value.asOf);
  const previousAsOfDate = new Date(`${asOfDate}T00:00:00Z`);
  previousAsOfDate.setUTCDate(previousAsOfDate.getUTCDate() - 1);
  const elapsedSinceKstMidnight = Date.parse(value.asOf) - Date.parse(`${asOfDate}T00:00:00+09:00`);
  /* maxDate 기본값을 API가 정한 직후 DB 조회가 KST 자정을 넘는 짧은 경합만 허용한다. */
  const crossedMidnight =
    actualMaxDate === previousAsOfDate.toISOString().slice(0, 10) &&
    elapsedSinceKstMidnight >= 0 &&
    elapsedSinceKstMidnight <= 5 * 60_000;
  if (
    (actualMaxDate !== undefined && actualMaxDate > asOfDate) ||
    (maxDate !== undefined && actualMaxDate !== maxDate) ||
    (maxDate === undefined && actualMaxDate !== asOfDate && !crossedMidnight)
  ) {
    throw new Error('장중 분석 귀결 points가 조회 기준일로 끝나지 않습니다.');
  }
  return { asOf: value.asOf, points };
}

export type IntradayOutcomeKind = 'collecting' | 'none' | 'complete' | 'partial' | 'missing';
export interface IntradayOutcome {
  kind: IntradayOutcomeKind;
  label: string;
  missing: number;
}

/** 서버 계수에서 표현 판정만 만든다. 현재일은 부분 결과를 실패로 확정하지 않는다. */
export function intradayOutcome(
  point: IntradayAnalysisTrendPointDto,
  currentKstDate: string,
): IntradayOutcome {
  if (point.date >= currentKstDate) return { kind: 'collecting', label: '집계 중', missing: 0 };
  if (point.triggers === 0) return { kind: 'none', label: '발화 없음', missing: 0 };
  if (point.results === point.triggers) return { kind: 'complete', label: '결과 생성 완료', missing: 0 };
  const missing = point.triggers - point.results;
  if (point.results > 0) return { kind: 'partial', label: `일부 미생성 ${missing}건`, missing };
  return { kind: 'missing', label: '결과 미생성', missing };
}

export function kstDateAt(iso: string): string {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit',
  }).format(new Date(iso));
}
