/* sources 도메인 — super-admin-api 연동 repository export.
 * 응답 원천은 운영 원장(ops_*) 실조회다(ALPHA-514) — mock 은 API 쪽에서 제거됐다. */
import { realSourcesRepository } from './repository.real';
import type { SourcesRepository } from './repository';

export const sourcesRepository: SourcesRepository = realSourcesRepository;

export * from './types';
export type { SourcesRepository } from './repository';
