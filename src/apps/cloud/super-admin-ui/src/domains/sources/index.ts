/* sources 도메인 — super-admin-api 연동 repository export.
 * mock 데이터는 API 쪽 mock 패키지가 반환한다 — 도메인별 DB 전환도 API 쪽에서 진행(ALPHA-515). */
import { realSourcesRepository } from './repository.real';
import type { SourcesRepository } from './repository';

export const sourcesRepository: SourcesRepository = realSourcesRepository;

export * from './types';
export type { SourcesRepository } from './repository';
