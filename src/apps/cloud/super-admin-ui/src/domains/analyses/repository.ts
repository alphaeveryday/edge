/* analyses 도메인 — repository 인터페이스 (mock·real 공통 계약) */
import type { Analysis } from './types';

export interface AnalysesRepository {
  list(): Promise<Analysis[]>;
  /**
   * 분석 무효화 — 사유 필수(ALPHA-440). 게시본을 내리고(WITHDRAWN) 테넌트에
   * INVALIDATION 이 전파된다. 미게시본은 서버가 409 로 거부한다.
   */
  invalidate(id: string, reason: string): Promise<void>;
}
