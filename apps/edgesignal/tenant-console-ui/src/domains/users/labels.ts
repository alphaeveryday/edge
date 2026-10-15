/* users 도메인 — 원장 role 4종의 화면 라벨(ADR-0025). 페이지·레이아웃이 공유한다. */
import type { MemberRole } from './types';

export const ROLE_LABEL: Record<MemberRole, string> = {
  TENANT_ADMIN: '관리자',
  COMPLIANCE_REVIEWER: '검수자',
  OPERATOR: '운영자',
  READ_ONLY: '읽기 전용',
};
