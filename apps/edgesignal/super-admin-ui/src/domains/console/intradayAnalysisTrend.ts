import type { IntradayAnalysisDto, IntradayAnalysisTrendDto, IntradayAnalysisTrendPointDto } from './types.ts';

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
  /* 옛 설명 엔진의 단계 필드는 검사하지 않는다(ALPHA-1330) — 화면이 읽지 않고 서버가 지운다. 그 필드를
   * 요구하면 지운 서버의 응답을 통째로 거부한다. 남은 축은 트리거 수와 새 분석 엔진의 단계다. */
  if (!validDate(value.date) || !count(value.triggers)) {
    throw new Error(`장중 분석 귀결 points[${index}]의 날짜·계수가 유효하지 않습니다.`);
  }
  const point = value as unknown as IntradayAnalysisTrendPointDto;
  /* 새 분석 엔진의 단계(ALPHA-1329) — 없어도 된다(이 필드를 싣기 전의 API). 오면 형상과 단계 불변식을
   * 본다: 각 단계는 앞 단계의 부분집합이고, 판정 보류는 앞 단계에 도달했는데 이 단계에 못 간 것이다. */
  if (value.analysis !== undefined && !validAnalysis(value.analysis, point.triggers)) {
    throw new Error(`장중 분석 귀결 points[${index}].analysis 가 유효하지 않습니다.`);
  }
  return { ...point };
}

function validAnalysis(value: unknown, triggers: number): value is IntradayAnalysisDto {
  if (!object(value) || typeof value.blind !== 'boolean') return false;
  const keys = ['requested', 'requestPending', 'started', 'startPending', 'completed', 'published'] as const;
  if (keys.some((key) => !count(value[key]))) return false;
  const a = value as unknown as IntradayAnalysisDto;
  return a.requested + a.requestPending <= triggers &&
    a.started + a.startPending <= a.requested &&
    a.completed <= a.started &&
    a.published <= a.completed;
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

export type IntradayOutcomeKind =
  | 'collecting' | 'none' | 'complete' | 'partial' | 'missing' | 'blind' | 'unavailable';
export interface IntradayOutcome {
  kind: IntradayOutcomeKind;
  label: string;
  missing: number;
}

/**
 * 서버 계수에서 표현 판정만 만든다. 현재일은 부분 결과를 실패로 확정하지 않는다.
 *
 * 새 분석 엔진의 "분석 완료"까지 갔는지를 본다(ALPHA-1329) — 옛 설명 엔진의 결과 단계는 10-02 이후
 * 0 이라 보지 않는다. 그 단계가 없는 응답(옛 API)은 상태 미제공, 새 분석 엔진의 요청이 아직 없던 날은
 * 관측 불가다 — 어느 쪽도 "미완료"로 그리지 않는다.
 */
export function intradayOutcome(
  point: IntradayAnalysisTrendPointDto,
  currentKstDate: string,
): IntradayOutcome {
  if (point.date >= currentKstDate) return { kind: 'collecting', label: '집계 중', missing: 0 };
  const analysis = point.analysis;
  if (!analysis) return { kind: 'unavailable', label: '상태 미제공', missing: 0 };
  if (analysis.blind) return { kind: 'blind', label: '관측 불가', missing: 0 };
  if (point.triggers === 0) return { kind: 'none', label: '발화 없음', missing: 0 };
  if (analysis.completed === point.triggers) return { kind: 'complete', label: '분석 완료', missing: 0 };
  /* 유예 시간 안의 트리거는 결손으로 확정하지 않는다 — 흐름 띠가 같은 트리거를 판정 보류로 두고 감소에서
   * 뺀다(ALPHA-1309). 지난 날도 그렇다: 자정 직전에 수용된 요청은 다음 날까지 유예 안이다. 다만 판정 보류가
   * 확정된 미완료를 가리면 안 된다 — 미완료는 판정 보류를 뺀 나머지다. */
  const pending = analysis.requestPending + analysis.startPending;
  const missing = point.triggers - analysis.completed - pending;
  if (missing === 0) return { kind: 'collecting', label: `판정 보류 ${pending}건`, missing: 0 };
  if (analysis.completed > 0 || pending > 0) return { kind: 'partial', label: `일부 미완료 ${missing}건`, missing };
  return { kind: 'missing', label: '분석 미완료', missing };
}

export function kstDateAt(iso: string): string {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit',
  }).format(new Date(iso));
}
