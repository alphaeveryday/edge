/* review 도메인 — 검수 실계약(/api/v1/review/items, snake_case 와이어)의 camelCase 투영.
 * explanations(mock) 도메인과 분리 — 검수 화면은 실 analysis_item 원장을 본다(ALPHA-436). */

/** 검수 사유 — screening_rule.rule_type 어휘(코드와 함께만 확장, ADR-0018). */
export type ReviewReasonType = 'BANNED_WORD' | 'SINGLE_SOURCE' | 'ASSERTIVE_EXPRESSION';

export interface ReviewItem {
  /** explanation_result_id (Cloud 발번 TEXT) */
  id: string;
  ticker: string | null;
  name: string | null;
  tradeDate: string | null;
  summary: string;
  headline: string | null;
  confidenceLevel: string | null;
  status: string;
  receivedAt: string | null;
  /** screening_check(result=REVIEW)의 rule_type 파생 */
  reviewReasons: string[];
}

/** 근거 문서 — 경계면 계약 형상(event-bundle) 그대로. */
export interface ReviewEvidence {
  kind: string;
  title: string;
  source: string;
  publishedAt: string | null;
}

export interface ReviewCheck {
  result: 'PASS' | 'REVIEW' | 'BLOCK';
  ruleType: string | null;
  matchedText: string | null;
  checkedAt: string | null;
}

export interface StatusChange {
  fromStatus: string | null;
  toStatus: string;
  actorType: 'SYSTEM' | 'MEMBER';
  actorName: string | null;
  reason: string | null;
  occurredAt: string | null;
}

export interface ReviewItemDetail extends ReviewItem {
  evidences: ReviewEvidence[];
  checks: ReviewCheck[];
  history: StatusChange[];
}
