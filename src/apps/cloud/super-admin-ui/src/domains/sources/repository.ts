/* sources 도메인 — repository 인터페이스 (mock·real 공통 계약) */
import type { SourceReport } from './types';

export interface SourcesRepository {
  /** @param runKey 볼 런의 슬롯 키. 없으면 최신 런 */
  report(runKey?: string): Promise<SourceReport>;
}
