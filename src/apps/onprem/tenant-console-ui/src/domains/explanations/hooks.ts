/* explanations 도메인 — 페이지가 사용하는 hook.
 * 페이지는 repository 를 직접 다루지 않고 이 hook 만 쓴다. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { explanationsRepository } from './index';

const KEY = ['explanations'];

export function useExplanations() {
  return useQuery({ queryKey: KEY, queryFn: () => explanationsRepository.list() });
}

export function useExplanation(id: string | undefined) {
  const query = useExplanations();
  return { ...query, explanation: query.data?.find((it) => it.id === id) };
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
    mutationFn: (id: string) => explanationsRepository.stop(id),
    onSuccess: invalidate,
  });
  const moveToReview = useMutation({
    mutationFn: (id: string) => explanationsRepository.moveToReview(id),
    onSuccess: invalidate,
  });

  return { updateFinal, stop, moveToReview };
}
