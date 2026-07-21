/* explanations 도메인 — 가격 변동 설명. mock·real 공유 타입.
 * 상태 코드는 도메인 상태기계(state-machine.md)의 어휘를 따르고,
 * 한글 라벨·배지 톤 매핑은 labels.ts(뷰 관심사)에 둔다. */

export type ServeStatus =
  | 'AUTO_PUBLISHED' // 자동 제공
  | 'REVIEW_REQUIRED' // 검수 대기
  | 'BLOCKED' // 점검 차단
  | 'REJECTED' // 검수 반려
  | 'STOPPED'; // 제공 중단 (운영자 수동)

export type RiskLevel = 'LOW' | 'MEDIUM' | 'HIGH';

export type Market = 'KRX' | 'NASDAQ';

export type ReviewReason = 'SINGLE_SOURCE' | 'BANNED_WORD' | 'ASSERTIVE';

export interface Evidence {
  type: '공시' | '뉴스';
  title: string;
  source: string;
  time: string;
}

export interface Explanation {
  id: number;
  name: string;
  code: string;
  market: Market;
  direction: 1 | -1;
  /** 등락률(%) 절대값 */
  changePct: number;
  status: ServeStatus;
  risk: RiskLevel;
  /** 검수 대기·차단·반려 사유 (해당 상태일 때만) */
  reviewReason?: ReviewReason;
  /** 반입 상대 시각 ("9분 전") */
  receivedRelative: string;
  /** 반입 절대 시각 ("2026-07-11 10:42 KST") */
  receivedAt: string;
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
