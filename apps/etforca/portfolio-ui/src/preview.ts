import type { Input, Job, Report } from './api';
import type { Saved, Workbook, Tracking } from './workbook';
export const preview = new URLSearchParams(location.search).get('preview');
export const jobKey = preview ? 'edge-preview-job' : 'edge-portfolio-job';
export const pendingKey = preview ? 'edge-preview-pending' : 'edge-portfolio-pending';
const workbookKey = preview === 'empty' ? 'edge-preview-empty-workbook' : 'edge-preview-workbook';
const startedAt = new Date(Date.now() - 45000).toISOString();
const section = { paragraphs: ['미국 주식의 성장 방향을 유지하면서 현금 30%로 추가매수 여력을 남겨 두세요.'], evidence: [], limitations: [] };
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
    historical_performance: { basis: '예시 데이터로 그린 성과 · 분배금 재투자', source_url: '', points: Array.from({ length: 24 }, (_, i) => ({ date: new Date(Date.UTC(2024, 10 + i, 1)).toISOString().slice(0, 10), recommended: 1000000 + i * 15000, current: 1000000 + i * 11000 })) } },
};
sample.assets.forEach((a, i) => { a.instrument_id = ['KR:360750', 'US:SCHD', 'KR:114260', 'KR:132030', 'KR:441640', 'CASH:KRW'][i]; });
const previewChats: unknown[] = [];
export async function previewRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const body = init?.body ? JSON.parse(String(init.body)) : null;
  let result: unknown;
  if (path === '/session') result = {};
  else if (path === '/catalog' || path === '/workbook/catalog') result = { fetched_at: report.as_of, counts: { KR: path === '/catalog' ? 1 : 2, US: path === '/catalog' ? 1 : 2 }, instruments: [{ id: 'KR:069500', market: 'KR', ticker: '069500', code: '069500', name: 'KODEX 200', kind: 'etf' }, { id: 'US:VOO', market: 'US', ticker: 'VOO', code: 'VOO', name: 'Vanguard S&P 500 ETF', kind: 'etf' }, { id: 'CASH:KRW', market: 'KR', ticker: 'KRW', code: 'KRW', name: '현금', kind: 'cash' }, ...(path === '/workbook/catalog' ? [{ id: 'KR:005930', market: 'KR', ticker: '005930', code: '005930', name: '삼성전자', kind: 'stock' }, { id: 'US:AAPL', market: 'US', ticker: 'AAPL', code: 'AAPL', name: 'Apple Inc. - Common Stock', kind: 'stock' }] : [])] };
  else if (path.startsWith('/drafts/')) result = { status: 'saved' };
  else if (path === '/workbook/newsletter' && !body) result = { subscribed: false };
  else if (path.endsWith('/newsletter')) result = { status: 'pending', delivery_ready: true };
  else if (path === '/newsletter/confirm') result = { status: 'confirmed', delivery_ready: true };
  else if (path === '/backtests') result = { id: 'preview-backtest' };
  else if (path.startsWith('/backtests/')) result = { state: 'succeeded', result: { history: report.recommendation!.historical_performance, return_pct: 34.5, excluded: [] } };
  else if (path === '/workbook/tracking') {
    const saved = await previewRequest<Saved>('/workbook');
    const w = saved.workbook;
    const assets = (w?.assets ?? []).map(a => {
      const price = a.category === '현금' ? null : a.price ?? sample.assets.find(s => s.instrument_id === a.instrument_id)?.price ?? (a.currency === 'USD' ? 500 : 50000);
      const rate = a.currency === 'USD' ? 1360 : 1;
      const quantity = a.quantity ?? (price && a.amount !== null ? a.amount / price / rate : null);
      return { id: a.id, value: price !== null && quantity !== null ? price * quantity * rate : a.amount, profit: price !== null && quantity !== null && a.average !== null ? (price - a.average) * quantity * rate - a.fee : null, price, price_date: price ? '2026-10-16' : null, quantity, average: a.average, dividend_yield_pct: a.category === '현금' ? null : 3.6, annual_dividend_per_share: price === null ? null : price * .036, dividend_cycle: '매월 · 최근 1년', estimated_quantity: a.quantity === null && price !== null };
    });
    const forecasts = (w?.assets ?? []).filter(a => a.category !== '현금').flatMap(a => Array.from({ length: 12 }, (_, i) => ({ asset_id: a.id, month: new Date(Date.UTC(2026, 9 + i, 1)).toISOString().slice(0, 7), amount: (assets.find(q => q.id === a.id)?.value ?? 0) * .003 })));
    result = { market: w ? { version: saved.version, payload: { date: '2026-10-18', assets, fx: 1360, forecasts, missing: assets.filter(a => a.value === null).map(a => a.id), dividend_missing: [] } } : null, days: assets.length && assets.every(a => a.value !== null) ? ['2026-10-16', '2026-10-17', '2026-10-18'].map((date, i) => ({ date, value: assets.reduce((s, a) => s + a.value!, 0) * (1 - (2 - i) * .005), assets, fx: 1360 })) : [], archives: (saved as Saved & { archives?: Tracking['archives'] }).archives ?? [] } satisfies Tracking;
  }
  else if (path === '/workbook') {
    const saved: Saved & { archives?: Tracking['archives'] } = JSON.parse(localStorage.getItem(workbookKey) || 'null') ?? { version: 0, workbook: null };
    if (body) { if (body.version !== saved.version) throw new Error('다른 탭에서 수정했어요. 저장된 내용을 다시 불러와 주세요.'); const archives = saved.archives ?? []; if (saved.workbook && ['dividends', 'months'].some(key => JSON.stringify(saved.workbook![key as 'dividends' | 'months']) !== JSON.stringify(body.workbook[key]))) archives.unshift({ version: saved.version, payload: saved.workbook }); result = { version: saved.version + 1, workbook: body.workbook, archives }; localStorage.setItem(workbookKey, JSON.stringify(result)); }
    else result = saved.workbook ? saved : { version: 0, workbook: preview === 'empty' ? null : sample };
  } else if (path.endsWith('/workbook')) result = sample;
  else if (path.endsWith('/report-request')) result = { status: 'received', delivery_status: 'not_scheduled' };
  else if (path.endsWith('/chats')) result = { limit: 5, items: previewChats };
  else if (path.endsWith('/chat') || path.startsWith('/analyses/preview-chat-')) {
    result = { id: path.endsWith('/chat') ? 'preview-chat-' + crypto.randomUUID() : path.split('/').at(-1), state: 'succeeded', input: body, created_at: new Date().toISOString(), result: { answer: { paragraphs: ['테스트 답변: 기존 테마를 유지하고 추가 투자금을 목표 비중에 맞춰 나누세요.'], evidence: [], limitations: [] }, revision: report, status: 'complete' } };
    if (path.endsWith('/chat') && body?.mode !== 'recovery') previewChats.push(result);
  }
  else if (path === '/analyses/latest' && preview === 'empty') result = null;
  else if (path.startsWith('/analyses')) {
    if (path === '/analyses' && body) localStorage.setItem('edge-preview-input', JSON.stringify(body as Input));
    const state = preview === 'queued' ? 'queued' : preview === 'running' ? 'running' : preview === 'failed' ? 'failed' : 'succeeded';
    const shown = structuredClone(report);
    if (preview === 'partial' && shown.recommendation) { shown.recommendation.dividend_yield_1y = null; shown.recommendation.historical_performance = null; }
    result = { id: 'preview', state, stage: state === 'running' ? 'portfolio' : null, result: state === 'succeeded' ? shown : null, failure: state === 'failed' ? 'TIMED_OUT' : null, started_at: startedAt } satisfies Job;
  } else throw new Error(`지원하지 않는 테스트 화면: ${path}`);
  return structuredClone(result) as T;
}
