/* analyses 도메인 — 상태 코드 → 한글 라벨·배지 톤 매핑 (뷰 관심사). */
import type { BadgeTone } from 'ui-kit';
import type { AnalysisConfidence, AnalysisPublicationStatus, AnalysisStatus } from './types';

export const ANALYSIS_STATUS_LABEL: Record<AnalysisStatus, string> = {
  COMPLETED: '분석 완료',
  PENDING: '분석 대기',
  FAILED: '분석 실패',
};

export const ANALYSIS_STATUS_TONE: Record<AnalysisStatus, BadgeTone> = {
  COMPLETED: 'active',
  PENDING: 'warn',
  FAILED: 'blocked',
};

/** 게시 상태 배지 — 실행 상태와 별개 축(ALPHA-737). 결과 없는 런(null)은 배지를 그리지 않는다. */
export const ANALYSIS_PUBLICATION_LABEL: Record<AnalysisPublicationStatus, string> = {
  PUBLISHED: '게시 중',
  DRAFT: '미게시',
  WITHDRAWN: '무효화됨',
};

export const ANALYSIS_PUBLICATION_TONE: Record<AnalysisPublicationStatus, BadgeTone> = {
  PUBLISHED: 'active',
  DRAFT: 'neutral',
  WITHDRAWN: 'blocked',
};

export const ANALYSIS_CONFIDENCE_LABEL: Record<AnalysisConfidence, string> = {
  HIGH: '높음',
  MEDIUM: '중간',
  LOW: '낮음',
};
