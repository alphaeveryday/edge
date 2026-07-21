/* users 도메인 — 사용자 및 권한. mock·real 공유 타입. */

export type MemberRole = 'Admin' | 'Compliance';

export type MemberStatus = 'ACTIVE' | 'INVITED';

export interface Member {
  id: number;
  name: string;
  email: string;
  role: MemberRole;
  status: MemberStatus;
  lastLogin: string;
}
