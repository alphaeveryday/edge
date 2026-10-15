/* tenants 도메인 — 테넌트(증권사) 관리. mock·real 공유 타입. */

/** IA 어휘(PoC/Production) — 'Dev' 는 어휘 정렬(수축 마이그레이션) 전 레거시 행 표기. */
export type TenantEnv = 'PoC' | 'Production' | 'Dev';

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
  /** 운영 메모(온보딩 기록) — 확장 전 행은 빈 문자열. */
  memo: string;
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

/** 생성 표면의 환경 어휘 — 레거시 Dev 는 읽기 전용 표기라 생성 선택지가 아니다. */
export type TenantCreateEnv = Exclude<TenantEnv, 'Dev'>;

export interface NewTenant {
  name: string;
  env: TenantCreateEnv;
  admin: string;
  email: string;
  memo: string;
}
