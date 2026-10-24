/* session 도메인 — 로그인 사용자·테넌트 컨텍스트. mock·real 공유 타입. */
import type { MemberRole } from '../users/types';

export interface SessionUser {
  name: string;
  email: string;
  role: MemberRole;
  tenantName: string;
  /** 초대 가능 이메일 도메인 */
  tenantDomain: string;
  /** 사이드바 로고 약칭 (예: "KB") */
  tenantMark: string;
}
