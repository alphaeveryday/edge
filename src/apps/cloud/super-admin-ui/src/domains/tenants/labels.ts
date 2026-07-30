/* tenants 도메인 — 상태 코드 → 한글 라벨·배지 톤 매핑 (뷰 관심사). */
import type { BadgeTone } from 'ui-kit';
import type { TenantEnv, TenantStatus } from './types';

export const TENANT_STATUS_LABEL: Record<TenantStatus, string> = {
  ACTIVE: '정상',
  SYNC_DELAYED: '동기화 지연',
  // 연결 상태는 Sync 채널 기준(IA) — 활성/비활성 개념이 아니다.
  ONBOARDING: '미연결(온보딩 중)',
};

export const TENANT_STATUS_TONE: Record<TenantStatus, BadgeTone> = {
  ACTIVE: 'active',
  SYNC_DELAYED: 'warn',
  ONBOARDING: 'neutral',
};

export const ENV_TONE: Record<TenantEnv, BadgeTone> = {
  Production: 'env',
  PoC: 'neutral',
  Dev: 'neutral', // 레거시 행(어휘 정렬 전)
};
