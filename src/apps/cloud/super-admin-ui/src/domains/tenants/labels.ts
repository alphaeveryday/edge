/* tenants 도메인 — 상태 코드 → 한글 라벨·배지 톤 매핑 (뷰 관심사). */
import type { BadgeTone } from 'ui-kit';
import type { TenantEnv, TenantStatus } from './types';

export const TENANT_STATUS_LABEL: Record<TenantStatus, string> = {
  ACTIVE: '정상',
  SYNC_DELAYED: '동기화 지연',
  ONBOARDING: '온보딩 중',
};

export const TENANT_STATUS_TONE: Record<TenantStatus, BadgeTone> = {
  ACTIVE: 'active',
  SYNC_DELAYED: 'warn',
  ONBOARDING: 'neutral',
};

export const ENV_TONE: Record<TenantEnv, BadgeTone> = {
  Prod: 'env',
  Dev: 'neutral',
};
