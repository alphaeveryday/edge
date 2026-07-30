/* analyses 도메인 — 상태 코드 → 한글 라벨·배지 톤 매핑 (뷰 관심사). */
import type { BadgeTone } from 'ui-kit';
import type { AnalysisConfidence, AnalysisStatus } from './types';

export const ANALYSIS_STATUS_LABEL: Record<AnalysisStatus, string> = {
  COMPLETED: '분석 완료',
  PENDING: '분석 대기',
  FAILED: '분석 실패',
  EXCLUDED: '제외됨',
};

export const ANALYSIS_STATUS_TONE: Record<AnalysisStatus, BadgeTone> = {
  COMPLETED: 'active',
  PENDING: 'warn',
  FAILED: 'blocked',
  EXCLUDED: 'neutral',
};

export const ANALYSIS_CONFIDENCE_LABEL: Record<AnalysisConfidence, string> = {
  HIGH: '높음',
  MEDIUM: '중간',
  LOW: '낮음',
};
