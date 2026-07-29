/* analyses 도메인 — repository 인터페이스 (mock·real 공통 계약) */
import type { Analysis } from './types';

export interface AnalysesRepository {
  list(): Promise<Analysis[]>;
  /** 분석 결과 정정 — 새 결과·정정 사유 필수(ALPHA-602). corrected 플래그가 함께 선다 */
  correct(id: string, result: string, reason: string): Promise<void>;
  /** 분석 대상 제외 — 제외 사유 필수. 테넌트에 분석 결과가 노출되지 않는다 */
  exclude(id: string, reason: string): Promise<void>;
  /** 제외 복원 — 사유는 선택 */
  restore(id: string): Promise<void>;
}
