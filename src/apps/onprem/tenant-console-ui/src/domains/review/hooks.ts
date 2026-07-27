/* review 도메인 — 페이지가 사용하는 hook. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { reviewRepository } from './index';

const LIST_KEY = ['review', 'items'];
const detailKey = (id: string) => ['review', 'items', id];

export function useReviewItems() {
  return useQuery({ queryKey: LIST_KEY, queryFn: () => reviewRepository.listPending() });
}

export function useReviewItem(id: string | undefined) {
  return useQuery({
    queryKey: detailKey(id ?? ''),
    queryFn: () => reviewRepository.detail(id!),
    enabled: !!id,
  });
}

export function useReviewActions(id: string) {
  const qc = useQueryClient();
  // 실패(동시 결정 409 등)도 원인이 낡은 화면이라 settle 시 무효화로 수렴한다.
  const settled = () => {
    qc.invalidateQueries({ queryKey: LIST_KEY });
    qc.invalidateQueries({ queryKey: detailKey(id) });
  };

  const approve = useMutation({
    mutationFn: (note: string | null) => reviewRepository.approve(id, note),
    onSettled: settled,
  });
  const approveEdited = useMutation({
    mutationFn: (p: { editedSummary: string; note: string | null }) =>
      reviewRepository.approveEdited(id, p.editedSummary, p.note),
    onSettled: settled,
  });
  const reject = useMutation({
    mutationFn: (reason: string) => reviewRepository.reject(id, reason),
    onSettled: settled,
  });
  const block = useMutation({
    mutationFn: (reason: string) => reviewRepository.block(id, reason),
    onSettled: settled,
  });

  return { approve, approveEdited, reject, block };
}
