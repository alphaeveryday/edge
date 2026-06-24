/* members 도메인 — mock·real 이 공유하는 타입 */

export type MemberRole = 'Owner' | 'Admin' | 'Developer' | 'Compliance' | 'Viewer';

export type MemberStatus = '활성' | '초대됨' | '비활성';

export interface Member {
  id: string;
  /** 아바타에 표시할 한 글자 (이름 첫 글자) */
  initial: string;
  name: string;
  email: string;
  role: MemberRole;
  /** 소속 앱 표시 문자열 (예: "전체", "MTS · 웹") */
  apps: string;
  status: MemberStatus;
  /** 최근 활동 표시 문자열 (예: "방금", "14분 전", "—") */
  lastActive: string;
}

export interface RoleSummary {
  name: MemberRole;
  desc: string;
  /** 해당 역할을 가진 구성원 수 */
  count: number;
  scope: string;
}

export interface InviteMemberInput {
  email: string;
  role: MemberRole;
}
