/* session 도메인 — 로그인 운영자 컨텍스트. mock·real 공유 타입. */

export interface OperatorSession {
  name: string;
  email: string;
  role: 'Owner';
  /** 아바타 이니셜 (예: "OP") */
  initials: string;
}
