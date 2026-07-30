/* explanations 도메인 — 상태 코드 → 한글 라벨·배지 톤 매핑 (뷰 관심사). */
import type { BadgeTone } from 'ui-kit';
import type { FeedState, ReviewReason, RiskLevel, ServeStatus } from './types';

export const STATUS_LABEL: Record<ServeStatus, string> = {
  AUTO_PUBLISHED: '자동 제공',
  APPROVED: '승인 제공',
  REVIEW_REQUIRED: '검수 대기',
  BLOCKED: '점검 차단',
  REJECTED: '검수 반려',
  UNPUBLISHED: '제공 중단',
};

export const STATUS_TONE: Record<ServeStatus, BadgeTone> = {
  AUTO_PUBLISHED: 'exposed',
  APPROVED: 'exposed',
  REVIEW_REQUIRED: 'warn',
  BLOCKED: 'blocked',
  REJECTED: 'blocked',
  UNPUBLISHED: 'gated',
};

/** 고객 화면에 실제 노출 중인 상태 — 제공 중단 액션은 이 상태에서만 의미가 있다 */
export const PUBLISHED_STATUSES: ServeStatus[] = ['AUTO_PUBLISHED', 'APPROVED'];

export const RISK_LABEL: Record<RiskLevel, string> = {
  LOW: '저위험',
  MEDIUM: '중위험',
  HIGH: '고위험',
};

export const RISK_TONE: Record<RiskLevel, BadgeTone> = {
  LOW: 'neutral',
  MEDIUM: 'warn',
  HIGH: 'blocked',
};

export const REASON_LABEL: Record<ReviewReason, string> = {
  SINGLE_SOURCE: '단일 출처',
  BANNED_WORD: '금칙어',
  ASSERTIVE: '단정 표현',
};

export const REASON_DESC: Record<ReviewReason, string> = {
  SINGLE_SOURCE: '근거 출처가 1개뿐입니다. 교차 확인 후 제공 여부를 판단하세요.',
  BANNED_WORD: '활성 금칙어와 일치하는 표현이 포함되어 있습니다. 문구를 수정하거나 반려하세요.',
  ASSERTIVE: '단정적 전망 표현이 감지되었습니다. 중립적 서술로 수정이 필요합니다.',
};

export const FEED_LABEL: Record<FeedState, string> = {
  NORMAL: '정상',
  DELAYED: '지연',
  STOPPED: '중단',
};

export const FEED_DOT_COLOR: Record<FeedState, string> = {
  NORMAL: 'var(--up)',
  DELAYED: 'var(--warn)',
  STOPPED: 'var(--down)',
};
