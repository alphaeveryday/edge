/* settings 도메인 — real 구현 (tenant-console-api 연동)
 *
 * 현재는 stub 이다. 백엔드 settings 엔드포인트가 완성되면 config/dataSources.ts 의
 * settings 를 'real' 로 바꾸는 것만으로 이 구현이 활성화된다. 응답 DTO 가 도메인
 * 타입과 다르면 여기서 매핑한다 (mock·real 의 반환 타입은 동일해야 한다).
 */
import { apiClient } from '../../api/client';
import type { SettingsRepository } from './repository';
import type { OrgProfile, PolicyPreset, NotificationSettings } from './types';

export const realSettingsRepository: SettingsRepository = {
  getOrg() {
    return apiClient.get<OrgProfile>('/settings/org');
  },

  listPresets() {
    return apiClient.get<PolicyPreset[]>('/settings/presets');
  },

  getNotifications() {
    return apiClient.get<NotificationSettings>('/settings/notifications');
  },

  updateNotifications(patch: Partial<NotificationSettings>) {
    return apiClient.patch<NotificationSettings>('/settings/notifications', patch);
  },
};
