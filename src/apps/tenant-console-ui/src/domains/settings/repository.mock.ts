/* settings 도메인 — mock 구현 (가상 증권사 "한빛투자증권" 기준)
 *
 * mock 도 반드시 Promise 를 반환한다(async). mock 과 real 의 로딩/에러 처리
 * 모양을 동일하게 유지하기 위함이다.
 */
import type { SettingsRepository } from './repository';
import type { OrgProfile, PolicyPreset, NotificationSettings } from './types';

/** 네트워크 지연을 흉내내 로딩 상태가 실제처럼 동작하게 한다. */
function delay<T>(value: T, ms = 280): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms));
}

const SEED_ORG: OrgProfile = {
  name: '한빛투자증권',
  bizno: '123-86-04521',
  domain: 'hanbit.co.kr',
  manager: '김도현',
  orgId: 'org_hanbit',
};

const SEED_PRESETS: PolicyPreset[] = [
  { name: '투자권유 면책 강화', ver: 'v2', state: '적용' },
  { name: '매매유도 표현 차단', ver: 'v2', state: '적용' },
  { name: '실적·전망 고지 규칙', ver: 'v1', state: '적용' },
  { name: '광고 심의 워크플로', ver: 'v1', state: '대기' },
];

/** 모듈 수명 동안 유지되는 in-memory 알림 상태 (갱신 시 누적). */
const notifications: NotificationSettings = { ev: true, quota: true, comp: true, weekly: false };

export const mockSettingsRepository: SettingsRepository = {
  getOrg() {
    return delay({ ...SEED_ORG });
  },

  listPresets() {
    return delay(SEED_PRESETS.map((p) => ({ ...p })));
  },

  getNotifications() {
    return delay({ ...notifications });
  },

  updateNotifications(patch: Partial<NotificationSettings>) {
    Object.assign(notifications, patch);
    return delay({ ...notifications });
  },
};
