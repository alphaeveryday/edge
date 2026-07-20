/* settings 도메인 — repository 인터페이스 (mock·real 공통 계약) */
import type { OrgProfile, PolicyPreset, NotificationSettings } from './types';

export interface SettingsRepository {
  /** 조직 프로필 */
  getOrg(): Promise<OrgProfile>;
  /** 규제 정책 프리셋 목록 */
  listPresets(): Promise<PolicyPreset[]>;
  /** 알림 설정 */
  getNotifications(): Promise<NotificationSettings>;
  /** 알림 설정 일부를 갱신하고 최종 상태를 반환 */
  updateNotifications(patch: Partial<NotificationSettings>): Promise<NotificationSettings>;
}
