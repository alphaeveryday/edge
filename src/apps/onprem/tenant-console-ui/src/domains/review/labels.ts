/* review 도메인 — 한글 라벨(뷰 관심사). 어휘 SSOT 는 스키마 CHECK·state-machine.md. */
import type { ConfidenceLevel } from '../explanations/types';
import { CONFIDENCE_LABEL } from '../explanations/labels';
import { confidenceGateReason, sourceGateReason } from '../screening/labels';
import type { GateCheck, ReviewCheck, ReviewReasonType } from './types';

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
 * 검사 한 행의 **사유** — 운영자가 설정한 기준의 문구를 그대로 쓴다(ALPHA-774).
 * 확신도를 `중간 이하` 로 걸었으면 사유도 `확신도 중간 이하` 여야 머리로 매핑할 일이 없다.
 *
 * 기준의 출처는 **판정 당시 정책 버전**이다 — matchedText 는 실측값이라(`source_events=1`)
 * 기준을 담지 않고, 오늘의 설정으로 과거 판정을 라벨링하면 감사 재현이 어긋난다.
 * 버전을 못 읽었으면(정책 결측) null 을 내고 화면은 실측값으로 떨어진다.
 */
export function checkReasonLabel(check: ReviewCheck): string | null {
  if (check.ruleType != null) return reasonLabel(check.ruleType);
  // 근거 없는 행은 두 가지다 — 스위치 OFF 로 검수행(REVIEW)과 청정 통과(PASS). 판정을 보지
  // 않으면 정상 통과가 "자동 제공 꺼짐"으로 표시된다(PolicyEvaluator 는 둘 다 근거 NULL).
  if (check.result !== 'REVIEW' && check.matchedText == null) return null;
  return gateReasonLabel(check);
}

/**
 * 룰 무관 REVIEW 판정의 사유 — 목록(GateCheck)과 상세(ReviewCheck)가 **같은 함수**를 쓴다.
 * 한쪽만 해석하면 같은 판정이 화면마다 다른 문구로 보인다(ALPHA-774).
 * 입력은 REVIEW 행 전제다 — 근거 없는 REVIEW 는 자동 제공 스위치 OFF 뿐이다.
 */
export function gateReasonLabel(gate: GateCheck): string | null {
  // 어느 게이트인지는 실측값의 축이 가른다.
  if (gate.matchedText?.startsWith('source_events=')) return sourceGateReason(gate.minSourceCount);
  if (gate.matchedText?.startsWith('confidence=')) return confidenceGateReason(gate.minConfidence);
  if (gate.matchedText === 'explanation_type=UNCERTAIN') return '원인 미확인 판정';
  if (gate.matchedText == null) return '자동 제공 꺼짐';
  return null;
}

/**
 * 검사 한 행의 **실측값** — 그 건이 실제로 얼마였나. 사유(기준)와 짝이다.
 * 어휘 밖 형식은 원문 그대로 낸다(Rule 12) — 해석 못 한 것을 숨기면 정책 결함이 사라진다.
 * 텍스트 매칭 룰의 근거는 운영자가 등록한 표현이라 어떤 문자열이든 올 수 있어 해석하지 않는다.
 */
export function matchedLabel(matchedText: string | null, ruleType?: string | null): string | null {
  // 빈 문자열은 결측이 아니다 — 그렇게 저장된 행이 있으면 그 자체가 드러나야 한다(Rule 12).
  if (matchedText == null) return null;
  if (ruleType != null && TEXT_MATCH_RULES.has(ruleType)) return `“${matchedText}”`;

  const sources = /^source_events=(\d+)$/.exec(matchedText);
  if (sources) return `출처 ${sources[1]}건`;

  // confidence=LOW<min=MEDIUM — 실측 확신도만 읽는다(기준은 정책 버전이 준다).
  const confidence = /^confidence=(\w+)<min=\w+$/.exec(matchedText);
  if (confidence) return `확신도 ${confidenceText(confidence[1])}`;

  // 원인 미확인은 "얼마였나"가 아니라 판정 그 자체다 — 사유가 이미 말하므로 실측은 없다
  // (사유·실측 두 칸에 같은 말이 반복되면 표가 정보를 두 번 쓴다).
  if (matchedText === 'explanation_type=UNCERTAIN') return null;

  return `“${matchedText}”`;
}

/** 근거가 "운영자가 등록한 표현"인 룰 타입 — 이쪽은 해석하지 않고 그대로 인용한다. */
const TEXT_MATCH_RULES = new Set(['BANNED_WORD', 'ASSERTIVE_EXPRESSION']);

/** 확신도 원값 → 표시 문구. 어휘는 explanations 배지(CONFIDENCE_LABEL)가 SSOT 다. */
function confidenceText(raw: string): string {
  if (raw === 'null') return '미산정';
  return Object.hasOwn(CONFIDENCE_LABEL, raw) ? CONFIDENCE_LABEL[raw as ConfidenceLevel] : raw;
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
