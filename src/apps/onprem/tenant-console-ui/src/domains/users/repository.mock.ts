/* users 도메인 — mock 구현. 시안(v0.2) 목데이터 이식. */
import type { UsersRepository } from './repository';
import type { Member } from './types';

const members: Member[] = [
  { id: 1, name: '조영서', email: 'youngseo.cho@kbsec.com', role: 'Admin', status: 'ACTIVE', lastLogin: '오늘 09:12' },
  { id: 2, name: '박성호', email: 'sungho.park@kbsec.com', role: 'Compliance', status: 'ACTIVE', lastLogin: '오늘 08:47' },
  { id: 3, name: '이수민', email: 'sumin.lee@kbsec.com', role: 'Admin', status: 'ACTIVE', lastLogin: '어제 17:20' },
  { id: 4, name: '최다혜', email: 'dahye.choi@kbsec.com', role: 'Compliance', status: 'ACTIVE', lastLogin: '2026-07-08' },
  { id: 5, name: '정민우', email: 'minwoo.jung@kbsec.com', role: 'Compliance', status: 'INVITED', lastLogin: '—' },
];
let nextId = 6;

export const mockUsersRepository: UsersRepository = {
  async list(): Promise<Member[]> {
    return members.map((m) => ({ ...m }));
  },
  async invite(email, role) {
    members.push({
      id: nextId++,
      name: email.split('@')[0],
      email,
      role,
      status: 'INVITED',
      lastLogin: '—',
    });
  },
};
