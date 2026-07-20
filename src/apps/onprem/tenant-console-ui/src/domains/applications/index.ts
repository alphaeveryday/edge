/* applications 도메인 — 진입점.
 * config/dataSources.ts 의 스위치를 보고 mock|real repository 중 하나만 export 한다.
 * 페이지/hook 은 이 모듈만 의존한다 (구현체를 직접 import 하지 않는다).
 */
import { DATA_SOURCES } from '../../config/dataSources';
import type { ApplicationsRepository } from './repository';
import { mockApplicationsRepository } from './repository.mock';
import { realApplicationsRepository } from './repository.real';

export const applicationsRepository: ApplicationsRepository =
  DATA_SOURCES.applications === 'real' ? realApplicationsRepository : mockApplicationsRepository;

export type {
  Application,
  WidgetKey,
  Webhook,
  CreateAppInput,
  IssueKeyInput,
  AddWebhookInput,
} from './types';
export {
  useApplications,
  useApplication,
  useWidgetKeys,
  useWebhooks,
  useUniverse,
} from './hooks';
