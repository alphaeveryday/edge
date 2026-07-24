/* sources 도메인 — repository 인터페이스 (mock·real 공통 계약) */
import type { SourceReport } from './types';

export interface SourcesRepository {
  report(): Promise<SourceReport>;
}
