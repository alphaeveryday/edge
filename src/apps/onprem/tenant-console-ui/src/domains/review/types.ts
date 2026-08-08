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
  /** 룰 무관 판정 재료 — 목록도 상세와 같은 사유 문구를 만든다(ALPHA-774). */
  gateChecks: GateCheck[];
}

/** 근거 문서 — 경계면 계약 형상(event-bundle) 그대로. */
export interface ReviewEvidence {
  kind: string;
  title: string;
  source: string;
  publishedAt: string | null;
  /** 원문 링크(ALPHA-739) — 계약상 optional, 결측이면 null(일반 텍스트 폴백). */
  sourceUri: string | null;
}

/** 목록의 룰 무관 판정 재료 — 상세와 같은 사유 문구를 만들기 위한 최소 필드(ALPHA-774). */
export interface GateCheck {
  matchedText: string | null;
  minSourceCount: number | null;
  minConfidence: string | null;
}

export interface ReviewCheck {
  result: 'PASS' | 'REVIEW' | 'BLOCK';
  ruleType: string | null;
  matchedText: string | null;
  /** 판정 당시 정책 — 검수 사유 문구의 기준 출처다(오늘 설정이 아니라 그때 값, ALPHA-774).
   * 버전 조회 실패 시 셋 다 null 이고 화면은 실측값만 보여준다. */
  policyVersionNo: number | null;
  minSourceCount: number | null;
  minConfidence: string | null;
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
