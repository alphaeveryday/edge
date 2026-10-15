/* users 도메인 — 사용자 및 권한. tenant-console-api member 원장 계약(ALPHA-119). */

/** 원장 role 4종(ADR-0025, ck_member_role). */
export type MemberRole = 'TENANT_ADMIN' | 'COMPLIANCE_REVIEWER' | 'OPERATOR' | 'READ_ONLY';

/** 관리자 직접 등록만이라 INVITED 없음 — 활성/비활성 2종. */
export type MemberStatus = 'ACTIVE' | 'INACTIVE';

export interface Member {
  id: number;
  name: string;
  email: string;
  role: MemberRole;
  status: MemberStatus;
  /** last_login_at ISO-8601 — 미로그인은 null. */
  lastLogin: string | null;
}
