/* analysisResults 도메인 — 새 분석 엔진(v2) 결과 목록·상세 (ALPHA-1333).
 * 필드는 super-admin-api `AnalysisResultRepository` 의 record 와 1:1 이다. 옛 설명 엔진 화면(`analyses`)과 별개다. */

export type AnalysisKind = 'outlook' | 'movement';
/** 행의 status 그대로다 — 콘솔이 새로 판정하지 않는다 */
export type AnalysisResultStatus = 'completed' | 'failed' | 'running';

export interface AnalysisResultFilter {
  kind: AnalysisKind;
  /** KST 날짜(YYYY-MM-DD). 비우면 서버가 오늘로 잡는다 */
  from?: string;
  to?: string;
  etf?: string;
  status?: AnalysisResultStatus;
  /** 합성 입력 등 data_source 가 database 가 아닌 행도 싣는다 */
  allSources?: boolean;
  page: number;
  size: number;
}

export interface AnalysisResultRow {
  analysisId: string;
  etfCode: string;
  etfName: string;
  analysisAt: string;
  status: AnalysisResultStatus;
  dataSource: string;
  /** 테넌트 조회가 게시본으로 내는가 — v2 테넌트 조회와 같은 기준 */
  published: boolean;
  publishedAt: string | null;
  /** 가격변동만 값이 있다. 전망은 회수 열이 없어 null */
  withdrawn: boolean | null;
  withdrawnAt: string | null;
  title: string | null;
  errorHead: string | null;
}

export interface AnalysisResultPage {
  total: number;
  page: number;
  size: number;
  rows: AnalysisResultRow[];
}

export interface OutlookItem {
  itemId: string;
  section: 'detail' | 'update';
  changeType: string | null;
  position: number;
  titleKeyword: string;
  bullets: unknown;
  sentence: string | null;
  sentiment: string | null;
  sourceLinks: unknown;
  sourceAsOf: string | null;
  toolRunIds: string[];
}

export interface OutlookDetail {
  sticker: string | null;
  summaryTitle: string | null;
  summary: string | null;
  detailTitle: string | null;
  detailMode: string | null;
  conclusionTitle: string | null;
  conclusionSentence: string | null;
  changeCondition: string | null;
  items: OutlookItem[];
  factors: { type: string; sticker: string; sentence: string }[];
  conclusionKeywords: { kind: string; position: number; label: string; toolRunIds: string[] }[];
}

export interface MovementItem {
  itemId: string;
  type: string;
  titleKeyword: string;
  sentence: string;
  sentiment: string;
  sourceAsOf: string | null;
  toolRunIds: string[];
}

export interface MovementDetail {
  tradingDate: string;
  summary: string | null;
  selectedItemIds: string[];
  withdrawn: boolean;
  withdrawnAt: string | null;
  items: MovementItem[];
}

export interface AnalysisResultDetail {
  analysisId: string;
  kind: AnalysisKind;
  etfCode: string;
  etfName: string;
  analysisAt: string;
  status: AnalysisResultStatus;
  dataSource: string;
  published: boolean;
  publishedAt: string | null;
  previousAnalysisId: string | null;
  errorMessage: string | null;
  outlook: OutlookDetail | null;
  movement: MovementDetail | null;
}
