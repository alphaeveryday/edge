/* applications 도메인 — real 구현 (tenant-console-api 연동)
 *
 * 현재는 stub 이다. 백엔드 applications 엔드포인트가 완성되면 config/dataSources.ts 의
 * applications 를 'real' 로 바꾸는 것만으로 이 구현이 활성화된다. 응답 DTO 가 도메인
 * 타입과 다르면 여기서 매핑한다 (mock·real 의 반환 타입은 동일해야 한다).
 */
import { apiClient } from '../../api/client';
import type { ApplicationsRepository } from './repository';
import type {
  Application,
  WidgetKey,
  Webhook,
  CreateAppInput,
  IssueKeyInput,
  AddWebhookInput,
} from './types';

export const realApplicationsRepository: ApplicationsRepository = {
  listApps() {
    return apiClient.get<Application[]>('/applications');
  },

  getApp(slug: string) {
    return apiClient.get<Application | null>(`/applications/${slug}`);
  },

  createApp(input: CreateAppInput) {
    return apiClient.post<Application>('/applications', input);
  },

  listKeys() {
    return apiClient.get<WidgetKey[]>('/widget-keys');
  },

  issueKey(input: IssueKeyInput) {
    return apiClient.post<WidgetKey>('/widget-keys', input);
  },

  regenerateKey(key: string) {
    return apiClient.post<WidgetKey>(`/widget-keys/${encodeURIComponent(key)}/regenerate`);
  },

  revokeKey(key: string) {
    return apiClient.delete<void>(`/widget-keys/${encodeURIComponent(key)}`);
  },

  listWebhooks() {
    return apiClient.get<Webhook[]>('/webhooks');
  },

  addWebhook(input: AddWebhookInput) {
    return apiClient.post<Webhook>('/webhooks', input);
  },

  getUniverse() {
    return apiClient.get<string[]>('/applications/universe');
  },
};
