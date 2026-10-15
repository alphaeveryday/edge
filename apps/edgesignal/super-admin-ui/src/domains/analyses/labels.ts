/* analyses 도메인 — 상태 코드 → 한글 라벨·배지 톤 매핑 (뷰 관심사). */
import type { BadgeTone } from 'ui-kit';
import type {
  AnalysisConfidence,
  AnalysisPublicationStatus,
  AnalysisStatus,
  EvidenceType,
  StatBand,
  StatBasis,
  StatMethod,
  StatUnit,
} from './types';

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

/* 근거 유형·통계검정 라벨 — 저장은 영문 코드, 한글은 여기서만 만든다(근거 포맷 명세 §10.3). */

export const EVIDENCE_TYPE_LABEL: Record<EvidenceType, string> = {
  PRICE: '가격',
  HOLDING: '구성종목',
  DISCLOSURE: '공시',
  NEWS: '뉴스',
  FINANCIAL: '재무및컨센서스',
  STAT_TEST: '통계검정',
};

/** chip 축약 라벨 — '재무및컨센서스' 는 flex-none chip 에 안 들어간다(명세 §10.2 C7). */
export const EVIDENCE_TYPE_CHIP: Record<EvidenceType, string> = {
  PRICE: '가격',
  HOLDING: '구성',
  DISCLOSURE: '공시',
  NEWS: '뉴스',
  FINANCIAL: '재무',
  STAT_TEST: '검정',
};

export const METHOD_LABEL: Record<StatMethod, string> = {
  SIMILAR_STOCKS: '비슷한종목비교',
  SIMILAR_DAYS: '비슷한날비교',
  SENSITIVE_STOCKS: '민감한종목비교',
  RELATED_STOCKS: '관계있는종목비교',
  BY_CONDITION: '조건별효과',
  VS_USUAL: '평소대비',
};

export const BASIS_LABEL: Record<StatBasis, string> = {
  MARKET: '시장 전체 움직임',
  SECTOR: '업종 전체 움직임',
  IDIO: '이 종목만의 움직임',
};

export const BAND_LABEL: Record<StatBand, string> = {
  TOP_TAIL: '과거보다 훨씬 큼',
  UPPER: '과거보다 큼',
  MIDDLE: '과거와 비슷한 수준',
  LOWER: '과거보다 작음',
  BOTTOM_TAIL: '과거보다 훨씬 작음',
};

export const UNIT_LABEL: Record<StatUnit, string> = { COUNT: '건', DAY: '일' };
