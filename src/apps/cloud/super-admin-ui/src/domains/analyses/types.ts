/* analyses 도메인 — 가격 변동 분석 (공통 분석 산출물 운영). 원장(explanation_*) 실조회 계약. */

export type AnalysisMarket = 'KRX' | 'NASDAQ';

/** 원장 confidence_level 어휘 그대로 — 화면이 새 판정을 만들지 않는다. */
export type AnalysisConfidence = 'HIGH' | 'MEDIUM' | 'LOW';

export type AnalysisStatus =
  | 'COMPLETED' // 분석 완료
  | 'PENDING' // 분석 대기 (근거 데이터 수집 중)
  | 'FAILED' // 분석 실패 (재시도 큐 등록)
  | 'EXCLUDED'; // 제외됨 (운영자 수동 — 테넌트 비노출)

export interface AnalysisEvidence {
  type: string;
  title: string;
  source: string;
  time: string;
}

export interface Analysis {
  id: string;
  name: string;
  code: string;
  market: AnalysisMarket;
  direction: 1 | -1;
  /** 등락률(%) 절대값 */
  changePct: number;
  status: AnalysisStatus;
  /** 변동 기준 시각 (목록 표시용 축약) */
  basisTime: string;
  basisTimeAbs: string;
  /** 분석 완료 시각 — 미완료면 '—' */
  doneTime: string;
  /** 분석 신뢰도 — 결과가 아직 없으면 null */
  confidence: AnalysisConfidence | null;
  /** 운영자 정정 여부 */
  corrected: boolean;
  result: string;
  evidence: AnalysisEvidence[];
  /** 이 분석의 근거 총 건수 — evidence 는 표시 상한까지만 담기므로 더 클 수 있다 */
  evidenceTotal: number;
}
