/* analyses 도메인 — super-admin-api 연동 repository export.
 * mock 데이터는 API 쪽 mock 패키지가 반환한다 — 도메인별 DB 전환도 API 쪽에서 진행(ALPHA-515). */
import { realAnalysesRepository } from './repository.real';
import type { AnalysesRepository } from './repository';

export const analysesRepository: AnalysesRepository = realAnalysesRepository;

export * from './types';
export * from './labels';
export type { AnalysesRepository } from './repository';
