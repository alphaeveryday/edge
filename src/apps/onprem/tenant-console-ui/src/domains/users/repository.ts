/* users 도메인 — repository 인터페이스 (real 계약) */
import type { Member, MemberRole } from './types';

/** 관리자 직접 등록 입력 — password 는 선택(있으면 데모 로컬 계정, 없으면 SSO 전용). */
export interface RegisterMemberInput {
  email: string;
  name: string;
  role: MemberRole;
  password?: string;
}

export interface UsersRepository {
  list(): Promise<Member[]>;
  register(input: RegisterMemberInput): Promise<Member>;
  deactivate(id: number): Promise<void>;
  /** 역할 부여·변경(ALPHA-499 API) — 자기 자신 대상은 서버가 403(직무 분리). */
  changeRole(id: number, role: MemberRole): Promise<void>;
}
