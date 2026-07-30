/* screening 도메인 — repository 인터페이스 (mock·real 공통 계약) */
import type { AutoPublishCriteria, BannedWord, NewBannedWord, PolicyVersionSummary } from './types';

export interface ScreeningRepository {
  listWords(): Promise<BannedWord[]>;
  addWord(word: NewBannedWord): Promise<void>;
  /** 활성/비활성 토글 */
  toggleWord(id: number): Promise<void>;
  getCriteria(): Promise<AutoPublishCriteria>;
  updateCriteria(patch: Partial<AutoPublishCriteria>): Promise<void>;
  getDisclaimer(): Promise<string>;
  updateDisclaimer(text: string): Promise<void>;
  /** 정책 버전 이력 — 최신 발행 순 */
  listVersions(): Promise<PolicyVersionSummary[]>;
}
