/* sources 도메인 — 페이지가 사용하는 hook. */
import { useQuery } from '@tanstack/react-query';
import { sourcesRepository } from './index';

/* 이 화면의 값어치는 "지금 상태"다. 특히 '실행 중' 배지는 작업이 도는 몇 분 동안만 참이라,
 * 안 갱신하면 처음 받은 값이 화면에 굳어 자기 목적을 부정한다(Codex #297).
 *
 * 30초인 이유: 조회는 최신 런 25행 집계 1회라 싸고, 파이프라인 스텝은 분 단위로 바뀐다.
 * `refetchIntervalInBackground` 는 기본 false 라 탭이 비활성이면 폴링이 멈춘다 — 아무도 안
 * 보는 동안 클라우드 DB 를 두드리지 않는다.
 *
 * 전역 QueryClient 가 아니라 **이 쿼리에만** 건다: 콘솔의 다른 도메인(tenants·analyses·
 * session)은 폴링하지 않고, 그 기본값을 바꾸는 건 이 티켓의 범위가 아니다(Rule 3). */
const REFETCH_MS = 30_000;

export function useSourceReport() {
  return useQuery({
    queryKey: ['sources'],
    queryFn: () => sourcesRepository.report(),
    refetchInterval: REFETCH_MS,
  });
}
