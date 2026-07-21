/* session 도메인 — mock 구현. 시안(v0.2)의 KB증권 데모 컨텍스트. */
import type { SessionRepository } from './repository';
import type { SessionUser } from './types';

const user: SessionUser = {
  name: '조영서',
  email: 'youngseo.cho@kbsec.com',
  role: 'Admin',
  tenantName: 'KB증권',
  tenantDomain: 'kbsec.com',
  tenantMark: 'KB',
};

export const mockSessionRepository: SessionRepository = {
  async current(): Promise<SessionUser> {
    return { ...user };
  },
  async updateDisplayName(name) {
    user.name = name;
  },
};
