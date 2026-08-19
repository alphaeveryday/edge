import type { EntityResolutionTrendDto, EntityResolutionTrendPointDto } from './types.ts';

const object = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const date = (value: unknown): value is string => {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(parsed.valueOf()) && parsed.toISOString().slice(0, 10) === value;
};

const count = (value: unknown): value is number =>
  typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;

function point(value: unknown, index: number): EntityResolutionTrendPointDto {
  if (!object(value)) throw new Error(`엔티티 해소율 points[${index}]가 객체가 아닙니다.`);
  const { date: observed, totalArguments: total, resolvedArguments: resolved, rate } = value;
  if (!date(observed)) throw new Error(`엔티티 해소율 points[${index}].date가 유효한 날짜가 아닙니다.`);
  if (!count(total) || !count(resolved) || resolved > total) {
    throw new Error(`엔티티 해소율 points[${index}]의 분자·분모가 유효하지 않습니다.`);
  }
  if (total === 0) {
    if (resolved !== 0 || rate !== null) {
      throw new Error(`엔티티 해소율 points[${index}]의 0/0 관측은 rate:null이어야 합니다.`);
    }
  } else {
    if (typeof rate !== 'number' || !Number.isFinite(rate) || rate < 0 || rate > 1) {
      throw new Error(`엔티티 해소율 points[${index}].rate가 유효한 비율이 아닙니다.`);
    }
    if (Math.abs(rate - resolved / total) > 1e-12) {
      throw new Error(`엔티티 해소율 points[${index}].rate가 분자·분모와 일치하지 않습니다.`);
    }
  }
  return { date: observed, totalArguments: total, resolvedArguments: resolved, rate: rate as number | null };
}

/** 손상된 와이어 응답을 판정 코드로 흘리지 않고 query 실패로 격리한다. */
export function parseEntityResolutionTrend(value: unknown, maxDate?: string): EntityResolutionTrendDto {
  if (!object(value) || !Array.isArray(value.points) || value.points.length > 10) {
    throw new Error('엔티티 해소율 응답의 points가 유효한 배열이 아닙니다.');
  }
  const points = value.points.map(point);
  for (let i = 0; i < points.length; i += 1) {
    if (i > 0 && points[i - 1].date >= points[i].date) {
      throw new Error('엔티티 해소율 points가 날짜 오름차순이 아닙니다.');
    }
    if (maxDate !== undefined && points[i].date > maxDate) {
      throw new Error('엔티티 해소율 points가 조회 기준일을 벗어났습니다.');
    }
  }
  return { points };
}
