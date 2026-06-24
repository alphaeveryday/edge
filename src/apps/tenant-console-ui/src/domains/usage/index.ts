/* usage 도메인 — 진입점.
 * config/dataSources.ts 의 스위치를 보고 mock|real repository 중 하나만 export 한다.
 * 페이지/hook 은 이 모듈만 의존한다 (구현체를 직접 import 하지 않는다).
 */
import { DATA_SOURCES } from '../../config/dataSources';
import type { UsageRepository } from './repository';
import { mockUsageRepository } from './repository.mock';
import { realUsageRepository } from './repository.real';

export const usageRepository: UsageRepository =
  DATA_SOURCES.usage === 'real' ? realUsageRepository : mockUsageRepository;

export type { UsageReport, CallSeries, AppShare, UsageByApp, Mau } from './types';
export { useUsage } from './hooks';
