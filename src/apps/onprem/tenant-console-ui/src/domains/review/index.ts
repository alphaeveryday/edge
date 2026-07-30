/* review 도메인 — tenant-console-api 검수 실계약 repository export */
import { realReviewRepository } from './repository.real';
import type { ReviewRepository } from './repository';

export const reviewRepository: ReviewRepository = realReviewRepository;

export * from './types';
export * from './labels';
export type { ReviewRepository } from './repository';
