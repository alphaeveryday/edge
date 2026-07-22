/* sources 도메인 — mock 구현. 시안(v0.1) 목데이터 이식. */
import type { SourcesRepository } from './repository';
import type { SourceReport } from './types';

const report: SourceReport = {
  checkedAt: '2026-07-12 10:20 KST',
  sources: [
    { name: '뉴스', provider: '연합인포맥스 · 뉴스핌 · Reuters 외 12개 매체', status: 'COLLECTING', lastCollected: '1분 전', volume: '8,214건' },
    { name: '공시', provider: 'DART · KIND', status: 'COLLECTING', lastCollected: '4분 전', volume: '342건' },
    { name: '시세', provider: 'KRX 시세 · NASDAQ TotalView', status: 'COLLECTING', lastCollected: '실시간 (지연 < 1s)', volume: '1,284만 틱' },
    { name: '실적 / 재무', provider: 'DART 재무 API · SEC EDGAR', status: 'DELAYED', lastCollected: '3시간 전', volume: '118건' },
    { name: 'ETF 구성 종목', provider: '운용사 PDF (KODEX · TIGER 외 34개사)', status: 'COLLECTING', lastCollected: '오늘 07:30', volume: '912개 ETF' },
  ],
};

export const mockSourcesRepository: SourcesRepository = {
  async report(): Promise<SourceReport> {
    return { checkedAt: report.checkedAt, sources: report.sources.map((s) => ({ ...s })) };
  },
};
