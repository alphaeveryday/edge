/* review 도메인 — repository 인터페이스 */
import type { ReviewItem, ReviewItemDetail } from './types';

export interface ReviewRepository {
  /** 검수 대기 목록 페이지(status=REVIEW_REQUIRED, 최근 수신 순 — 서버 정렬·limit/offset, ALPHA-914) */
  listPending(params: { limit: number; offset: number }): Promise<ReviewItem[]>;
  detail(id: string): Promise<ReviewItemDetail>;
  approve(id: string, note: string | null): Promise<void>;
  approveEdited(id: string, editedSummary: string, note: string | null): Promise<void>;
  reject(id: string, reason: string): Promise<void>;
  block(id: string, reason: string): Promise<void>;
}
