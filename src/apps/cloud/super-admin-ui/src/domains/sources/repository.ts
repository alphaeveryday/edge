/* sources 도메인 — repository 인터페이스 (mock·real 공통 계약) */
import type { SourceGrid, SourceReport } from './types';

export interface SourcesRepository {
  /** @param runKey 볼 런의 슬롯 키. 없으면 최신 런 */
  report(runKey?: string): Promise<SourceReport>;
  /** @param days 격자 조회 창(일). 없으면 서버 기본(30일) */
  grid(days?: number): Promise<SourceGrid>;
}
