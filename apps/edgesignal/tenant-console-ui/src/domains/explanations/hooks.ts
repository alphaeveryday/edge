/* explanations 도메인 — 페이지가 사용하는 hook.
 * 페이지는 repository 를 직접 다루지 않고 이 hook 만 쓴다. */
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError } from '../../api/client';
import { LIST_PAGE_SIZE, nextOffset } from '../../lib/pagination';
import { explanationsRepository } from './index';

const KEY = ['explanations'];

/** 목록 무한 스크롤(ALPHA-914) — 50개 단위 서버 페이지. data 는 로드된 페이지의 평탄화. */
export function useExplanations() {
  const query = useInfiniteQuery({
    queryKey: KEY,
    queryFn: ({ pageParam }) => explanationsRepository.list({ limit: LIST_PAGE_SIZE, offset: pageParam }),
    initialPageParam: 0,
    getNextPageParam: nextOffset,
  });
  return { ...query, data: query.data?.pages.flat() };
}

/** 상세 단건 — 목록이 페이지 단위라 딥링크는 목록 캐시에 의존할 수 없다(ALPHA-914). */
export function useExplanation(id: string | undefined) {
  const query = useQuery({
    queryKey: [...KEY, id],
    queryFn: () => explanationsRepository.get(id!),
    enabled: !!id,
    // 404 도 결정적 응답 — 재시도해 봐야 '찾을 수 없음' 화면만 늦어진다. 로컬 retry 는
    // 전역 기본값을 덮어쓰므로 전역이 제외하는 401/403(main.tsx)도 함께 보존한다.
    retry: (failureCount, err) =>
      !(err instanceof ApiError && [401, 403, 404].includes(err.status)) && failureCount < 3,
  });
  return { ...query, explanation: query.data };
}

/** 상태별 건수 — 대시보드 KPI·사이드바 검수 대기 배지. */
export function useStatusCounts() {
  return useQuery({
    queryKey: [...KEY, 'status-counts'],
    queryFn: () => explanationsRepository.statusCounts(),
  });
}

export function useFeedStatus() {
  return useQuery({ queryKey: ['feed-status'], queryFn: () => explanationsRepository.feedStatus() });
}

/** 목록·상세·검수 화면의 상태 변경 액션 모음. 성공 시 목록 캐시를 무효화한다. */
export function useExplanationActions() {
  const qc = useQueryClient();
  const invalidate = () => {
    // KEY prefix 무효화가 목록·상세·status-counts 를 함께 덮는다.
    qc.invalidateQueries({ queryKey: KEY });
    // 검수 이관은 검수 대기열에 항목을 더한다 — 검수 목록도 함께 갱신(ALPHA-914).
    qc.invalidateQueries({ queryKey: ['review', 'items'] });
  };

  const updateFinal = useMutation({
    mutationFn: ({ id, final }: { id: string; final: string }) =>
      explanationsRepository.updateFinal(id, final),
    onSuccess: invalidate,
  });
  const stop = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      explanationsRepository.stop(id, reason),
    onSuccess: invalidate,
  });
  const moveToReview = useMutation({
    mutationFn: (id: string) => explanationsRepository.moveToReview(id),
    onSuccess: invalidate,
  });

  return { updateFinal, stop, moveToReview };
}
