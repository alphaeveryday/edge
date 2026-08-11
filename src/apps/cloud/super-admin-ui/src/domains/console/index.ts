/* console 도메인 — 규칙 엔진의 사실 공급(ALPHA-738 · ADR-0050).
 * 응답 원천은 운영·설명·전달 원장 실조회다 — mock 은 없다. */
import { realConsoleRepository } from './repository.real';
import type { ConsoleRepository } from './repository';

export const consoleRepository: ConsoleRepository = realConsoleRepository;

export * from './types';
export type { ConsoleRepository } from './repository';
