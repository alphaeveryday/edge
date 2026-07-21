/* explanations 도메인 — repository 인터페이스 (mock·real 공통 계약) */
import type { Explanation, FeedStatus } from './types';

export interface ExplanationsRepository {
  /** 가격 변동 설명 전체 (대시보드·목록·상세가 공유) */
  list(): Promise<Explanation[]>;
  /** 반입(수신) 상태 */
  feedStatus(): Promise<FeedStatus>;
  /** 최종 제공 문구 수정 (상세 화면) */
  updateFinal(id: number, final: string): Promise<void>;
  /** 제공 중단 (운영자 수동) */
  stop(id: number): Promise<void>;
  /** 점검 차단 건을 검수 대기열로 이관 */
  moveToReview(id: number): Promise<void>;
  /** 검수 승인 후 제공 — 최종 문구·검수 의견 반영 */
  approve(id: number, final: string, note: string): Promise<void>;
  /** 검수 반려 */
  reject(id: number, note: string): Promise<void>;
  /** 검수 중 최종 문구 임시 저장 */
  saveDraft(id: number, final: string): Promise<void>;
}
