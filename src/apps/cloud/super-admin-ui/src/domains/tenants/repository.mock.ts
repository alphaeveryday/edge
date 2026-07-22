/* tenants 도메인 — mock 구현. 시안(v0.1) 목데이터 이식. */
import type { TenantsRepository } from './repository';
import type { Tenant } from './types';

/* 시안의 시드 PRNG 이식 — 시간대별 호출량 24칸 (07~10시 구간 피크, 지연 시 최근 급감) */
function bars(seed: number, lag: boolean): number[] {
  let x = seed;
  const arr: number[] = [];
  for (let i = 0; i < 24; i++) {
    x = (x * 9301 + 49297) % 233280;
    let v = 18 + Math.round((x / 233280) * 78);
    if (i >= 7 && i <= 10) v = Math.min(96, v + 24);
    if (lag && i > 19) v = 4;
    arr.push(v);
  }
  return arr;
}

const tenants: Tenant[] = [
  { id: 't1', name: '미래에셋증권', domain: 'miraeasset.com', env: 'Prod', status: 'ACTIVE', admin: '김도현', email: 'dohyun.kim@miraeasset.com', created: '2025-11-03', lastSync: '2분 전', lastSyncAbs: '2026-07-12 10:22 KST', calls: 48214, errors: 12, bars: bars(11, false) },
  { id: 't2', name: '한국투자증권', domain: 'koreainvestment.com', env: 'Prod', status: 'ACTIVE', admin: '박서연', email: 'sy.park@koreainvestment.com', created: '2025-12-18', lastSync: '1분 전', lastSyncAbs: '2026-07-12 10:23 KST', calls: 39082, errors: 4, bars: bars(23, false) },
  { id: 't3', name: 'NH투자증권', domain: 'nhqv.com', env: 'Prod', status: 'SYNC_DELAYED', admin: '이준호', email: 'jh.lee@nhqv.com', created: '2026-01-22', lastSync: '47분 전', lastSyncAbs: '2026-07-12 09:37 KST', calls: 21540, errors: 138, bars: bars(37, true) },
  { id: 't4', name: '삼성증권', domain: 'samsungpop.com', env: 'Prod', status: 'ACTIVE', admin: '최유진', email: 'yj.choi@samsungpop.com', created: '2026-02-09', lastSync: '3분 전', lastSyncAbs: '2026-07-12 10:21 KST', calls: 35617, errors: 9, bars: bars(51, false) },
  { id: 't5', name: 'KB증권', domain: 'kbsec.com', env: 'Dev', status: 'ONBOARDING', admin: '정민재', email: 'mj.jung@kbsec.com', created: '2026-06-30', lastSync: '—', lastSyncAbs: '동기화 이력 없음', calls: 1204, errors: 0, bars: bars(67, false).map((v) => Math.round(v * 0.15)) },
  { id: 't6', name: '키움증권', domain: 'kiwoom.com', env: 'Prod', status: 'ACTIVE', admin: '한지우', email: 'jw.han@kiwoom.com', created: '2026-03-14', lastSync: '2분 전', lastSyncAbs: '2026-07-12 10:22 KST', calls: 52931, errors: 21, bars: bars(83, false) },
  { id: 't7', name: '신한투자증권', domain: 'shinhansec.com', env: 'Dev', status: 'SYNC_DELAYED', admin: '오세훈', email: 'sh.oh@shinhansec.com', created: '2026-04-02', lastSync: '1시간 12분 전', lastSyncAbs: '2026-07-12 09:12 KST', calls: 8462, errors: 64, bars: bars(97, true) },
  { id: 't8', name: '하나증권', domain: 'hanaw.com', env: 'Dev', status: 'ONBOARDING', admin: '임수아', email: 'sa.lim@hanaw.com', created: '2026-07-08', lastSync: '—', lastSyncAbs: '동기화 이력 없음', calls: 316, errors: 2, bars: bars(113, false).map((v) => Math.round(v * 0.08)) },
];
let nextId = 9;

export const mockTenantsRepository: TenantsRepository = {
  async list(): Promise<Tenant[]> {
    return tenants.map((t) => ({ ...t, bars: [...t.bars] }));
  },
  async create(input) {
    tenants.unshift({
      id: `t${nextId++}`,
      name: input.name,
      domain: input.email.split('@')[1] ?? '',
      env: input.env,
      status: 'ONBOARDING',
      admin: input.admin,
      email: input.email,
      created: '2026-07-22',
      lastSync: '—',
      lastSyncAbs: '동기화 이력 없음',
      calls: 0,
      errors: 0,
      bars: Array(24).fill(2),
    });
  },
};
