/* members 도메인 — mock 구현 (가상 증권사 "한빛투자증권" 기준)
 *
 * mock 도 반드시 Promise 를 반환한다(async). mock 과 real 의 로딩/에러 처리
 * 모양을 동일하게 유지하기 위함이다.
 */
import type { MembersRepository } from './repository';
import type { Member, RoleSummary, InviteMemberInput } from './types';

/** 네트워크 지연을 흉내내 로딩 상태가 실제처럼 동작하게 한다. */
function delay<T>(value: T, ms = 280): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms));
}

const SEED_MEMBERS: Member[] = [
  { id: 'mem_1', initial: '김', name: '김도현', email: 'dohyun@hanbit.co.kr', role: 'Owner', apps: '전체', status: '활성', lastActive: '방금' },
  { id: 'mem_2', initial: '이', name: '이서연', email: 'seoyeon@hanbit.co.kr', role: 'Admin', apps: '전체', status: '활성', lastActive: '14분 전' },
  { id: 'mem_3', initial: '박', name: '박준호', email: 'junho@hanbit.co.kr', role: 'Developer', apps: 'MTS · 웹', status: '활성', lastActive: '1시간 전' },
  { id: 'mem_4', initial: '최', name: '최민정', email: 'minjung@hanbit.co.kr', role: 'Compliance', apps: '전체', status: '활성', lastActive: '어제' },
  { id: 'mem_5', initial: '정', name: '정우성', email: 'woo@hanbit.co.kr', role: 'Viewer', apps: '리서치랩', status: '초대됨', lastActive: '—' },
  { id: 'mem_6', initial: '한', name: '한지민', email: 'jimin@hanbit.co.kr', role: 'Developer', apps: '웹', status: '활성', lastActive: '3시간 전' },
];

const SEED_ROLES: RoleSummary[] = [
  { name: 'Owner', desc: '조직 설정·삭제 포함 전체 권한', count: 1, scope: '전체' },
  { name: 'Admin', desc: '앱·구성원·정책 관리', count: 1, scope: 'Organization 관리 · 앱 관리' },
  { name: 'Developer', desc: '키·웹훅·SDK·분석 설정', count: 2, scope: '앱 설정 · 키 발급' },
  { name: 'Compliance', desc: '감사 로그·면책·키워드·검수', count: 1, scope: '컴플라이언스 전체' },
  { name: 'Viewer', desc: '읽기 전용 대시보드', count: 1, scope: '읽기 전용' },
];

/** 모듈 수명 동안 유지되는 in-memory 상태 (초대 시 누적). */
const members: Member[] = [...SEED_MEMBERS];

let seq = members.length;

export const mockMembersRepository: MembersRepository = {
  list() {
    return delay(members.map((m) => ({ ...m })));
  },

  listRoles() {
    return delay(SEED_ROLES.map((r) => ({ ...r })));
  },

  invite({ email, role }: InviteMemberInput) {
    const trimmed = email.trim();
    const local = trimmed.split('@')[0] || '신규';
    const created: Member = {
      id: `mem_${++seq}`,
      initial: local[0]?.toUpperCase() ?? '신',
      name: local,
      email: trimmed,
      role,
      apps: '—',
      status: '초대됨',
      lastActive: '—',
    };
    members.push(created);
    return delay({ ...created });
  },
};
