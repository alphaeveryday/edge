import type { Input, Job, Report } from './api';
import type { Saved, Workbook } from './workbook';
export const preview = new URLSearchParams(location.search).get('preview');
export const jobKey = preview ? 'edge-preview-job' : 'edge-portfolio-job';
export const pendingKey = preview ? 'edge-preview-pending' : 'edge-portfolio-pending';
const workbookKey = preview === 'empty' ? 'edge-preview-empty-workbook' : 'edge-preview-workbook';
const startedAt = new Date(Date.now() - 45000).toISOString();
const section = { paragraphs: ['미국 주식의 성장 방향을 유지하면서 채권과 현금으로 추가매수 여력을 확보하세요.'], evidence: [], limitations: [] };
export const sample: Workbook = {
  title: '내 포트폴리오', fx: 1360,
  assets: [
    ['미국시장 ETF 예시', 'ISA', '주식', 'KRW', 400, 15000, 17200, 1, 35, 3000],
    ['배당성장 ETF 예시', '일반 계좌', '주식', 'USD', 80, 25, 28, 1300, 20, 5000],
    ['국채 ETF 예시', 'IRP', '채권', 'KRW', 100, 100000, 102000, 1, 30, 2000],
    ['금 ETF 예시', '연금저축', '금·원자재', 'KRW', 100, 13000, 15000, 1, 10, 1000],
    ['월분배 ETF 예시', '일반 계좌', '주식', 'KRW', 200, 10000, 9800, 1, 5, 2000],
  ].map<Workbook['assets'][number]>((r, i) => ({ id: String(i), name: String(r[0]), account: String(r[1]), category: String(r[2]), currency: r[3] as 'KRW' | 'USD', quantity: Number(r[4]), average: Number(r[5]), price: Number(r[6]), cost_fx: Number(r[7]), target: Number(r[8]), fee: Number(r[9]), amount: null, weight: null, price_date: '2026-10-15' })).concat([{ id: 'cash', name: '현금', account: '일반 계좌', category: '현금', currency: 'KRW', quantity: null, average: null, price: null, cost_fx: null, target: null, fee: 0, amount: 2000000, weight: null, price_date: null }]),
  dividends: [{ id: 'd1', asset_id: '0', date: '2026-10-08', status: '실수령', amount: 31000 }, { id: 'd2', asset_id: '1', date: '2026-10-15', status: '실수령', amount: 54400 }, { id: 'd3', asset_id: '4', date: '2026-10-25', status: '예상', amount: 43000 }],
  months: [{ month: '2026-08', value: 24000000, deposit: 0, withdrawal: 0, return_pct: 1.8 }, { month: '2026-09', value: 25000000, deposit: 0, withdrawal: 0, return_pct: null }, { month: '2026-10', value: 25586400, deposit: 0, withdrawal: 0, return_pct: null }],
};
const report: Report = {
  schema_version: 2, sections: { question: section, portfolio: section, outlook: section, opinion: section }, sources: [], model: '화면 확인용 예시', as_of: '2026-10-17T00:00:00Z', elapsed_seconds: 0,
  metrics: { total_krw: '25586400', holdings: sample.assets.map(a => ({ name: a.name, account: a.account, weight_pct: null })), unavailable_reason: '' },
  recommendation: { title: '성장 방향을 유지하는 분산 구성', total_krw: '25586400', evaluation: { scores: { leadership: 4, profitability: 4, stability: 3 }, overall: 4, method: '예시', reason: section },
    allocations: [{ instrument_id: 'US:VOO', name: '미국시장 ETF 예시', ticker: 'VOO', kind: 'etf', account: 'ISA', weight_pct: 70, amount_krw: '17910480' }, { instrument_id: 'CASH:KRW', name: '현금', ticker: 'KRW', kind: 'cash', account: '일반 계좌', weight_pct: 30, amount_krw: '7675920' }],
    actions: [{ instrument_id: 'US:VOO', name: '미국시장 ETF 예시', ticker: 'VOO', account: 'ISA', before_pct: 60, after_pct: 70, action: '비중 확대', buy_krw: '1000000' }], expected_return_1y: null, dividend_yield_1y: 1.5, unavailable_reason: '',
    historical_performance: { basis: '화면 확인용 가상 성과 · 분배금 재투자', source_url: '', points: Array.from({ length: 24 }, (_, i) => ({ date: new Date(Date.UTC(2024, 10 + i, 1)).toISOString().slice(0, 10), recommended: 1000000 + i * 15000, current: 1000000 + i * 11000 })) } },
};
export async function previewRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const body = init?.body ? JSON.parse(String(init.body)) : null;
  let result: unknown;
  if (path === '/session') result = {};
  else if (path === '/catalog') result = { fetched_at: report.as_of, counts: { KR: 1, US: 1 }, instruments: [{ id: 'KR:069500', market: 'KR', ticker: '069500', code: '069500', name: 'KODEX 200', kind: 'etf' }, { id: 'US:VOO', market: 'US', ticker: 'VOO', code: 'VOO', name: 'Vanguard S&P 500 ETF', kind: 'etf' }, { id: 'CASH:KRW', market: 'KR', ticker: 'KRW', code: 'KRW', name: '현금', kind: 'cash' }] };
  else if (path.startsWith('/drafts/')) result = { status: 'saved' };
  else if (path.endsWith('/newsletter')) result = { status: 'pending', delivery_ready: true };
  else if (path === '/newsletter/confirm') result = { status: 'confirmed', delivery_ready: true };
  else if (path === '/workbook') {
    const saved: Saved = JSON.parse(localStorage.getItem(workbookKey) || 'null') ?? { version: 0, workbook: null };
    if (body) { if (body.version !== saved.version) throw new Error('다른 탭에서 수정했어요. 저장된 내용을 다시 불러와 주세요.'); result = { version: saved.version + 1, workbook: body.workbook }; localStorage.setItem(workbookKey, JSON.stringify(result)); }
    else result = saved.workbook ? saved : { version: 0, workbook: preview === 'empty' ? null : sample };
  } else if (path.endsWith('/workbook')) result = sample;
  else if (path.endsWith('/report-request')) result = { status: 'received', delivery_status: 'not_scheduled' };
  else if (path.endsWith('/chat') || path.startsWith('/analyses/preview-chat-')) {
    result = { id: path.endsWith('/chat') ? 'preview-chat-' + crypto.randomUUID() : path.split('/').at(-1), state: 'succeeded', result: { answer: { paragraphs: ['테스트 답변: 기존 테마를 유지하고 추가 투자금을 목표 비중에 맞춰 나누세요.'], evidence: [], limitations: [] }, revision: report, status: 'complete' } };
  }
  else if (path.startsWith('/analyses')) {
    if (path === '/analyses' && body) localStorage.setItem('edge-preview-input', JSON.stringify(body as Input));
    const state = preview === 'queued' ? 'queued' : preview === 'running' ? 'running' : preview === 'failed' ? 'failed' : 'succeeded';
    const shown = structuredClone(report);
    if (preview === 'partial' && shown.recommendation) { shown.recommendation.dividend_yield_1y = null; shown.recommendation.historical_performance = null; }
    result = { id: 'preview', state, stage: state === 'running' ? 'portfolio' : null, result: state === 'succeeded' ? shown : null, failure: state === 'failed' ? 'TIMED_OUT' : null, started_at: startedAt } satisfies Job;
  } else throw new Error(`지원하지 않는 테스트 화면: ${path}`);
  return structuredClone(result) as T;
}
