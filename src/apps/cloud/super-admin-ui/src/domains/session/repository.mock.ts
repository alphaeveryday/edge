/* session 도메인 — mock 구현. 시안(v0.1)의 운영자 컨텍스트. */
import type { SessionRepository } from './repository';
import type { OperatorSession } from './types';

const session: OperatorSession = {
  name: 'EDGE 운영팀',
  email: 'ops@edge.io',
  role: 'Owner',
  initials: 'OP',
};

export const mockSessionRepository: SessionRepository = {
  async current(): Promise<OperatorSession> {
    return { ...session };
  },
  async updateDisplayName(name) {
    session.name = name;
  },
};
