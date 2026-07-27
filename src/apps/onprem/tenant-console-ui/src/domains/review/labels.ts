/* review 도메인 — 한글 라벨(뷰 관심사). 어휘 SSOT 는 스키마 CHECK·state-machine.md. */
import type { ReviewReasonType } from './types';

export const REASON_LABEL: Record<ReviewReasonType, string> = {
  BANNED_WORD: '금칙어',
  SINGLE_SOURCE: '단일 출처',
  ASSERTIVE_EXPRESSION: '단정 표현',
};

/** 미지의 rule_type(신규 릴리스 선반영 등)은 원어 그대로 노출 — 조용히 숨기지 않는다. */
export function reasonLabel(ruleType: string): string {
  return REASON_LABEL[ruleType as ReviewReasonType] ?? ruleType;
}

export const CHECK_RESULT_LABEL: Record<string, string> = {
  PASS: '통과',
  REVIEW: '검수',
  BLOCK: '차단',
};

export const EVIDENCE_KIND_LABEL: Record<string, string> = {
  DISCLOSURE: '공시',
  NEWS: '뉴스',
};

export const ITEM_STATUS_LABEL: Record<string, string> = {
  RECEIVED: '수신',
  AUTO_PUBLISHED: '자동 제공',
  REVIEW_REQUIRED: '검수 대기',
  APPROVED: '승인 제공',
  REJECTED: '검수 반려',
  BLOCKED: '점검 차단',
  UNPUBLISHED: '제공 중단',
  CORRECTED: '정정됨',
  INVALIDATED: '무효화',
};
