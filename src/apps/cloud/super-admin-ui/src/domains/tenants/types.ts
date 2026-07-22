/* tenants 도메인 — 테넌트(증권사) 관리. mock·real 공유 타입. */

export type TenantEnv = 'Prod' | 'Dev';

export type TenantStatus =
  | 'ACTIVE' // 정상
  | 'SYNC_DELAYED' // 동기화 지연
  | 'ONBOARDING'; // 온보딩 중 (동기화 이력 없음)

export interface Tenant {
  id: string;
  name: string;
  domain: string;
  env: TenantEnv;
  status: TenantStatus;
  admin: string;
  email: string;
  created: string;
  lastSync: string;
  lastSyncAbs: string;
  /** 최근 24시간 호출 수 */
  calls: number;
  /** 최근 24시간 오류 수 */
  errors: number;
  /** 시간대별 호출량 24칸 (오래된 것 → 현재) */
  bars: number[];
}

export interface NewTenant {
  name: string;
  env: TenantEnv;
  admin: string;
  email: string;
  memo: string;
}
