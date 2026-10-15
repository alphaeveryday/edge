/* tenants 도메인 — repository 인터페이스 (mock·real 공통 계약) */
import type { NewTenant, Tenant } from './types';

export interface TenantsRepository {
  list(): Promise<Tenant[]>;
  /** 테넌트 생성 — 연결 상태는 온보딩(동기화 이력 없음)으로 시작 */
  create(input: NewTenant): Promise<void>;
}
