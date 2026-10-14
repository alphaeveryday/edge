/* analysisResults 도메인 — super-admin-api 연동 구현 (ALPHA-1333) */
import { apiClient } from '../../api/client';
import type { AnalysisResultsRepository } from './repository';
import type { AnalysisResultDetail, AnalysisResultPage } from './types';

export const realAnalysisResultsRepository: AnalysisResultsRepository = {
  list: (f) => {
    const query = new URLSearchParams({ kind: f.kind, page: String(f.page), size: String(f.size) });
    if (f.from) query.set('from', f.from);
    if (f.to) query.set('to', f.to);
    if (f.etf) query.set('etf', f.etf);
    if (f.status) query.set('status', f.status);
    if (f.allSources) query.set('allSources', 'true');
    return apiClient.get<AnalysisResultPage>(`/analysis-results?${query.toString()}`);
  },
  detail: (kind, id) =>
    apiClient.get<AnalysisResultDetail>(
      `/analysis-results/${encodeURIComponent(kind)}/${encodeURIComponent(id)}`,
    ),
};
