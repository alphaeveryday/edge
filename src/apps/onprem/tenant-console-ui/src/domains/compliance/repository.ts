/* compliance 도메인 — repository 인터페이스 (mock·real 공통 계약) */
import type { DisclaimerDoc, Keyword } from './types';

export interface ComplianceRepository {
  /** 현재 면책 문구 */
  getDisclaimer(): Promise<DisclaimerDoc>;
  /** 면책 문구 발행 — 버전을 올리고(v4→v5) 갱신된 문서를 반환 */
  publishDisclaimer(text: string): Promise<DisclaimerDoc>;
  /** 키워드 블랙리스트 목록 */
  listKeywords(): Promise<Keyword[]>;
  /** 차단 단어 추가 — 갱신된 목록을 반환 */
  addKeyword(word: string): Promise<Keyword[]>;
  /** 차단 단어 제거 — 갱신된 목록을 반환 */
  removeKeyword(word: string): Promise<Keyword[]>;
  /** 오늘 차단된 출력 건수 */
  getBlockedToday(): Promise<number>;
}
