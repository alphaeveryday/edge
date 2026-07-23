/* session 도메인 — repository 인터페이스 (mock·real 공통 계약) */
import type { SessionUser } from './types';

export interface SessionRepository {
  current(): Promise<SessionUser>;
  updateDisplayName(name: string): Promise<void>;
  /** 서버 세션 무효화 (POST /auth/logout) */
  logout(): Promise<void>;
}
