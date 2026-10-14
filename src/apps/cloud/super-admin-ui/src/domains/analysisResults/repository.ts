/* analysisResults 도메인 — repository 인터페이스 */
import type { AnalysisKind, AnalysisResultDetail, AnalysisResultFilter, AnalysisResultPage } from './types';

export interface AnalysisResultsRepository {
  list(filter: AnalysisResultFilter): Promise<AnalysisResultPage>;
  detail(kind: AnalysisKind, id: string): Promise<AnalysisResultDetail>;
}
