/* review 도메인 — 한글 라벨(뷰 관심사). 어휘 SSOT 는 스키마 CHECK·state-machine.md. */
import type { ConfidenceLevel } from '../explanations/types';
import { CONFIDENCE_LABEL } from '../explanations/labels';
import type { ReviewReasonType } from './types';

export const REASON_LABEL: Record<ReviewReasonType, string> = {
  BANNED_WORD: '금칙어',
  SINGLE_SOURCE: '단일 출처',
  ASSERTIVE_EXPRESSION: '단정 표현',
};

/** 룰 무관 REVIEW(자동 제공 기준 미충족)의 서버 파생 마커 — rule_type 어휘 밖 상수. */
export const AUTO_PUBLISH_CRITERIA = 'AUTO_PUBLISH_CRITERIA';

/** 미지의 rule_type(신규 릴리스 선반영 등)은 원어 그대로 노출 — 조용히 숨기지 않는다. */
export function reasonLabel(ruleType: string): string {
  if (ruleType === AUTO_PUBLISH_CRITERIA) return '자동 제공 기준 미충족';
  return REASON_LABEL[ruleType as ReviewReasonType] ?? ruleType;
}

/**
 * 매칭 근거(screening_check.matched_text) → 운영자 문구. 원장은 감사 재현을 위해 판정기가
 * 남긴 원값을 그대로 보관하는데(PolicyEvaluator), 게이트 판정의 원값은 기계 문자열이라
 * 화면에 그대로 나가면 안 된다 — `source_events=2` 를 읽고 무엇이 걸렸는지 알 수 없다.
 *
 * **해석은 게이트 판정에만 한다.** 텍스트 매칭 룰(금칙어·단정 표현)의 근거는 운영자가 등록한
 * 표현 그 자체라 어떤 문자열이든 올 수 있다 — 누가 `source_events=2` 를 금칙어로 등록하면
 * 표현 매칭이 "출처 2건"으로 둔갑한다. 그래서 ruleType 으로 먼저 가른다.
 *
 * 어휘 밖 형식은 **원문 그대로** 낸다(Rule 12) — 해석 못 한 것을 숨기면 정책 결함이나
 * 새 판정 축이 화면에서 사라진다.
 */
export function matchedLabel(matchedText: string | null, ruleType?: string | null): string | null {
  // 빈 문자열은 결측이 아니다 — 그렇게 저장된 행이 있으면 그 자체가 드러나야 한다(Rule 12).
  if (matchedText == null) return null;
  if (ruleType != null && TEXT_MATCH_RULES.has(ruleType)) return `“${matchedText}”`;

  const sources = /^source_events=(\d+)$/.exec(matchedText);
  if (sources) return `출처 ${sources[1]}건`;

  // confidence=LOW<min=MEDIUM — 실제 확신도와 기준을 한 문장으로. 결측이면 판정기가
  // 문자열 "null" 을 이어 붙인다(Java 문자열 연결)라 그것도 어휘로 다룬다.
  const confidence = /^confidence=(\w+)<min=(\w+)$/.exec(matchedText);
  if (confidence) {
    return `확신도 ${confidenceText(confidence[1])} · 기준 ${confidenceText(confidence[2])}`;
  }

  if (matchedText === 'explanation_type=UNCERTAIN') return '원인 미확인 판정';

  return `“${matchedText}”`;
}

/** 근거가 "운영자가 등록한 표현"인 룰 타입 — 이쪽은 해석하지 않고 그대로 인용한다. */
const TEXT_MATCH_RULES = new Set(['BANNED_WORD', 'ASSERTIVE_EXPRESSION']);

/** 확신도 원값 → 표시 문구. 어휘는 explanations 배지(CONFIDENCE_LABEL)가 SSOT 다. */
function confidenceText(raw: string): string {
  if (raw === 'null') return '미산정';
  return Object.hasOwn(CONFIDENCE_LABEL, raw)
    ? CONFIDENCE_LABEL[raw as ConfidenceLevel]
    : raw;
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
  // CORRECTED 는 폐지 어휘(ADR-0044)지만 append-only 상태 이력의 과거 행 표시에 필요해 라벨만 유지.
  CORRECTED: '정정됨',
  INVALIDATED: '무효화',
};
