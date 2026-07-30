/* explanations 도메인 — repository 인터페이스 (mock·real 공통 계약) */
import type { Explanation, FeedStatus } from './types';

export interface ExplanationsRepository {
  /** 가격 변동 설명 전체 (대시보드·목록·상세가 공유) */
  list(): Promise<Explanation[]>;
  /** 반입(수신) 상태 */
  feedStatus(): Promise<FeedStatus>;
  /** 최종 제공 문구 수정 (상세 화면) */
  updateFinal(id: string, final: string): Promise<void>;
  /** 제공 중단 (운영자 수동) — 사유 필수 (감사·publication unpublish_reason) */
  stop(id: string, reason: string): Promise<void>;
  /** 점검 차단 건을 검수 대기열로 이관 */
  moveToReview(id: string): Promise<void>;
}
