/* analyses 도메인 — 가격 변동 분석 (공통 분석 산출물 운영). mock·real 공유 타입. */

export type AnalysisMarket = 'KRX' | 'NASDAQ';

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
  /** 영향도 0~100 */
  score: number;
  /** 운영자 정정 여부 */
  corrected: boolean;
  result: string;
  evidence: AnalysisEvidence[];
}
