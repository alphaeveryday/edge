/* explanations 도메인 — 페이지가 사용하는 hook.
 * 페이지는 repository 를 직접 다루지 않고 이 hook 만 쓴다. */
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
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
  const invalidate = () => qc.invalidateQueries({ queryKey: KEY });

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
