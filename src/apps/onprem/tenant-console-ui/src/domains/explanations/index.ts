/* explanations 도메인 — tenant-console-api 연동 repository export.
 * mock 데이터는 API 쪽 mock 패키지가 반환한다 — 도메인별 DB 전환도 API 쪽에서 진행(ALPHA-513). */
import { realExplanationsRepository } from './repository.real';
import type { ExplanationsRepository } from './repository';

export const explanationsRepository: ExplanationsRepository = realExplanationsRepository;

export * from './types';
export * from './labels';
export type { ExplanationsRepository } from './repository';
