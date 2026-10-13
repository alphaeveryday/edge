import type { AnalysisTrendDto, AnalysisTrendKind, AnalysisTrendPointDto } from './types.ts';

const object = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const date = (value: unknown): value is string => {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(parsed.valueOf()) && parsed.toISOString().slice(0, 10) === value;
};

const count = (value: unknown): value is number =>
  typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;

const KINDS: AnalysisTrendKind[] = ['outlook', 'movement'];

function point(value: unknown, where: string, from: string, to: string): AnalysisTrendPointDto {
  if (!object(value)) throw new Error(`분석 추이 ${where}가 객체가 아닙니다.`);
  const { date: day, completed, failed, running, completedEtfs, failedEtfs } = value;
  if (!date(day) || day < from || day > to) {
    throw new Error(`분석 추이 ${where}.date가 조회 범위의 날짜가 아닙니다.`);
  }
  if (![completed, failed, running, completedEtfs, failedEtfs].every(count)) {
    throw new Error(`분석 추이 ${where}의 건수가 유효하지 않습니다.`);
  }
  /* 종 수는 그 상태의 행 수를 넘을 수 없다 — 넘으면 두 열이 뒤바뀐 응답이다 */
  if ((completedEtfs as number) > (completed as number) || (failedEtfs as number) > (failed as number)) {
    throw new Error(`분석 추이 ${where}의 ETF 종 수가 건수보다 큽니다.`);
  }
  return { date: day, completed, failed, running, completedEtfs, failedEtfs } as AnalysisTrendPointDto;
}

/** 손상된 와이어 응답을 화면으로 흘리지 않고 query 실패로 격리한다. */
export function parseAnalysisTrend(value: unknown): AnalysisTrendDto {
  if (!object(value) || !date(value.from) || !date(value.to) || value.from > value.to) {
    throw new Error('분석 추이 응답의 조회 범위가 유효하지 않습니다.');
  }
  const { from, to } = value;
  if (!Array.isArray(value.kinds)) throw new Error('분석 추이 응답의 kinds가 배열이 아닙니다.');
  const kinds = KINDS.map((kind) => {
    const found = (value.kinds as unknown[]).find((k) => object(k) && k.kind === kind);
    if (!object(found) || !Array.isArray(found.points)) {
      throw new Error(`분석 추이 응답에 ${kind} 계열이 없습니다.`);
    }
    const points = found.points.map((p, i) => point(p, `${kind}.points[${i}]`, from, to));
    for (let i = 1; i < points.length; i += 1) {
      if (points[i - 1].date >= points[i].date) {
        throw new Error(`분석 추이 ${kind}.points가 날짜 오름차순이 아닙니다.`);
      }
    }
    return { kind, points };
  });
  return { from, to, kinds };
}
