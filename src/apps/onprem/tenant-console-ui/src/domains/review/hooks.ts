/* review 도메인 — 페이지가 사용하는 hook. */
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { LIST_PAGE_SIZE, nextOffset } from '../../lib/pagination';
import { reviewRepository } from './index';

const LIST_KEY = ['review', 'items'];
const detailKey = (id: string) => ['review', 'items', id];

/** 검수 대기 무한 스크롤(ALPHA-914) — 50개 단위 서버 페이지, 최근 수신 순. */
export function useReviewItems() {
  const query = useInfiniteQuery({
    queryKey: LIST_KEY,
    queryFn: ({ pageParam }) => reviewRepository.listPending({ limit: LIST_PAGE_SIZE, offset: pageParam }),
    initialPageParam: 0,
    getNextPageParam: nextOffset,
  });
  return { ...query, data: query.data?.pages.flat() };
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
    // 검수 결정은 explanations 상태(status-counts 의 REVIEW_REQUIRED 배지 포함)를
    // 바꾼다 — prefix 무효화로 목록·상세·집계를 함께 갱신한다(ALPHA-914).
    qc.invalidateQueries({ queryKey: ['explanations'] });
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
