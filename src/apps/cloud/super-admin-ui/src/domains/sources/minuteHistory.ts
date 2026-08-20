import type { MinuteSession, MinuteStatus } from './types';

export const minuteStatusQueryKey = (date?: string) => ['sources', 'minute', date ?? null] as const;

export function shouldFetchMinuteDetail(
  requestedDate: string | undefined,
  latestDate: string | undefined,
  mock: boolean,
): boolean {
  return Boolean(requestedDate) && requestedDate !== latestDate && !mock;
}

export function minuteDetailData(
  requestedDate: string | undefined,
  latest: MinuteStatus | undefined,
  latestUpdatedAt: number,
  dated: MinuteStatus | undefined,
  datedUpdatedAt: number,
): MinuteStatus | undefined {
  const matchingLatest = latest?.date === requestedDate ? latest : undefined;
  const matchingDated = dated?.date === requestedDate ? dated : undefined;
  if (matchingLatest && matchingDated) {
    return datedUpdatedAt > latestUpdatedAt ? matchingDated : matchingLatest;
  }
  return matchingLatest ?? matchingDated ?? dated ?? latest;
}

export type MinuteDetailState =
  | { kind: 'loading' }
  | { kind: 'error' }
  | { kind: 'stale' }
  | {
      kind: 'ready';
      minute: MinuteStatus;
      sessions: MinuteSession[];
      refreshFailed: boolean;
    };

/**
 * 선택 날짜 상세의 조회 상태를 세션 부재와 분리한다.
 *
 * 같은 날짜의 직전 실측은 갱신 실패 뒤에도 보존하지만, 다른 날짜 응답은 절대 선택 날짜의
 * 세션으로 쓰지 않는다. 빈 sessions 는 matching 응답을 받은 뒤에만 실제 부재다.
 */
export function resolveMinuteDetail(
  requestedDate: string,
  dataset: string,
  data: MinuteStatus | undefined,
  isPending: boolean,
  isError: boolean,
): MinuteDetailState {
  if (data?.date === requestedDate) {
    return {
      kind: 'ready',
      minute: data,
      sessions: data.sessions.filter((session) => session.dataset === dataset),
      refreshFailed: isError,
    };
  }
  if (data) return { kind: 'stale' };
  if (isError) return { kind: 'error' };
  if (isPending) return { kind: 'loading' };
  return { kind: 'loading' };
}
