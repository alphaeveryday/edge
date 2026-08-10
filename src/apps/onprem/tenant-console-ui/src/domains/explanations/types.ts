/* explanations 도메인 — 가격 변동 설명. mock·real 공유 타입.
 * 상태 코드는 도메인 상태기계(state-machine.md)의 어휘를 따르고,
 * 한글 라벨·배지 톤 매핑은 labels.ts(뷰 관심사)에 둔다. */

export type ServeStatus =
  | 'AUTO_PUBLISHED' // 자동 제공 (정책 통과)
  | 'APPROVED' // 승인 제공 (검수자 승인)
  | 'REVIEW_REQUIRED' // 검수 대기
  | 'BLOCKED' // 점검 차단
  | 'REJECTED' // 검수 반려
  | 'UNPUBLISHED'; // 제공 중단 (운영자 수동 — publication 상태)

/** 엔진 확신도(LLM confidence: 높음/중간/보류) — 원장 confidence_level 원값. */
export type ConfidenceLevel = 'LOW' | 'MEDIUM' | 'HIGH';

export type Market = 'KRX' | 'NASDAQ';

export type ReviewReason = 'SINGLE_SOURCE' | 'BANNED_WORD' | 'ASSERTIVE';

export interface Evidence {
  type: '공시' | '뉴스';
  title: string;
  source: string;
  time: string;
  /** 원문 링크(ALPHA-739) — 결측(EOD 뉴스 구멍 등)이면 생략, UI 는 일반 텍스트 폴백. */
  sourceUri?: string;
}

export interface Explanation {
  /** 원장 설명 ID(explanation_result_id) — 실전환 후 문자열(ALPHA-607). */
  id: string;
  name: string;
  code: string;
  status: ServeStatus;
  /** 확신도 — 원장 confidence_level 결측 시 생략(위험등급 재라벨 폐지, ALPHA-634). */
  confidence?: ConfidenceLevel;
  /** 검수 대기·차단·반려 사유 (해당 상태일 때만) */
  reviewReason?: ReviewReason;
  /** 반입 상대 시각 ("9분 전") */
  receivedRelative: string;
  /** 반입 절대 시각 ("2026-07-11 10:42 KST") */
  receivedAt: string;
  /** 스냅샷 기준시각 ("2026-07-11 16:00 KST") — 다스냅샷 공존 판별 축(ALPHA-744) */
  explanationAsOf: string;
  /** 콘텐츠 기준시각 — 산문이 서술하는 창의 끝(ALPHA-918). 결측(구형 수신분)이면 explanationAsOf 폴백 표시 */
  contentAsOf?: string;
  /** 현재 노출 head 여부 — 서버가 서빙 술어("유효 최신 승리")로 판정, UI 파생 금지(ALPHA-744) */
  serving: boolean;
  evidence: Evidence[];
  /** 모델이 생성한 원본 설명 문구 */
  original: string;
  /** 최종 제공 문구 (검수·수정 반영본) */
  final: string;
}

export type FeedState = 'NORMAL' | 'DELAYED' | 'STOPPED';

export interface FeedStatus {
  state: FeedState;
  lastReceivedRelative: string;
  todayReceived: number;
}
