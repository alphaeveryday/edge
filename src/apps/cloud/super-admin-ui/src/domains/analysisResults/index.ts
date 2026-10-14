/* analysisResults 도메인 — super-admin-api 연동 repository export */
import { realAnalysisResultsRepository } from './repository.real';
import type { AnalysisResultsRepository } from './repository';

export const analysisResultsRepository: AnalysisResultsRepository = realAnalysisResultsRepository;

export * from './types';
export type { AnalysisResultsRepository } from './repository';
