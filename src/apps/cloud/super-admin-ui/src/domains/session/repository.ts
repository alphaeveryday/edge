/* session 도메인 — repository 인터페이스 (mock·real 공통 계약) */
import type { OperatorSession } from './types';

export interface SessionRepository {
  current(): Promise<OperatorSession>;
  updateDisplayName(name: string): Promise<void>;
}
