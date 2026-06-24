/* compliance 도메인 — 진입점.
 * config/dataSources.ts 의 스위치를 보고 mock|real repository 중 하나만 export 한다.
 * 페이지/hook 은 이 모듈만 의존한다 (구현체를 직접 import 하지 않는다).
 */
import { DATA_SOURCES } from '../../config/dataSources';
import type { ComplianceRepository } from './repository';
import { mockComplianceRepository } from './repository.mock';
import { realComplianceRepository } from './repository.real';

export const complianceRepository: ComplianceRepository =
  DATA_SOURCES.compliance === 'real' ? realComplianceRepository : mockComplianceRepository;

export type { DisclaimerDoc, Keyword } from './types';
export { useCompliance } from './hooks';
