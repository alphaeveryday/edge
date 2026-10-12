export const roles = ['question', 'portfolio', 'outlook', 'opinion'] as const;
export type Role = typeof roles[number];
export const titles: Record<Role, string> = { question: '질문에 대한 답', portfolio: '현재 포트폴리오 분석', outlook: '앞으로의 전망', opinion: '투자 의견' };
export type Holding = { name: string; account: string; amount_krw: string | null; kind?: 'cash' | 'etf'; instrument_id?: string };
export type Instrument = { id: string; market: string; ticker: string; code: string; name: string; kind: 'etf' | 'cash' };
export type Catalog = { fetched_at: string; counts: { KR: number; US: number }; instruments: Instrument[] };
export type Input = { draft_id?: string; holdings: Holding[]; question: string; horizon: string; input_mode?: string; planning?: boolean; preferences?: { accounts: string[]; contribution: string; interests: string[]; note: string; tax_profile: import('./TaxAccounts').TaxProfile | null } };
export type Section = { paragraphs: string[]; evidence: { source_id: string; quote: string }[]; limitations: string[] };
export type Evaluation = { scores: { leadership: number; profitability: number; stability: number } | null; overall: number | null; reason: Section; method: string };
export type Allocation = { instrument_id: string; name: string; ticker: string; kind: 'cash' | 'etf'; account: string; weight_pct: number; amount_krw: string | null };
export type History = { points: { date: string; recommended: number; current: number | null }[]; basis: string; source_url: string; current_unavailable_reason?: string; interval?: '1d'; currency?: 'KRW'; initial_krw?: number; method?: string; sources?: { instrument_id: string; url: string }[] };
export type Recommendation = { title: string; allocations: Allocation[]; evaluation: Evaluation; total_krw: string | null; actions: { instrument_id: string; name: string; ticker: string; account: string; before_pct: number | null; after_pct: number; action: string }[]; expected_return_1y: number | null; dividend_yield_1y: number | null; historical_performance: History | null; unavailable_reason: string; target_return_1y?: number; dividend_basis?: string; return_forecast?: { reason: string; method: string }; dividend_observations?: { instrument_id: string; as_of: string; trailing_yield_pct: number; source_url?: string; method?: string; evidence: { url: string; quote: string }[] }[] };
export type Report = {
  schema_version?: number;
  current_evaluation?: Evaluation;
  recommendation?: Recommendation;
  insights?: { title: string; content: Section }[];
  sections: Record<Role, Section>;
  metrics: { holdings: { name: string; account: string; weight_pct: number | null }[]; total_krw: string | null; unavailable_reason: string };
  sources: { id: string; url: string; fetched_at: string }[];
  model: string; as_of: string; elapsed_seconds: number;
};
export type Job = { id: string; state: 'queued' | 'running' | 'succeeded' | 'failed'; stage: Role | null; result: Report | null; failure: string | null; started_at: string | null };

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch('/api/portfolio' + path, { ...init, signal: AbortSignal.timeout(15000), headers: { 'Content-Type': 'application/json', ...init?.headers } });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    if (body.detail === 'CHAT_LIMIT') throw new Error('CHAT_LIMIT');
    if (response.status === 422) throw new Error('목록에서 ETF 또는 현금을 선택하고 금액을 확인해 주세요.');
    if (response.status === 429) throw new Error('현재 요청이 많거나 오늘 체험 횟수를 모두 사용했어요. 잠시 후 다시 시도해 주세요.');
    if (response.status === 401) throw new Error('분석을 시작한 브라우저에서 다시 열어 주세요.');
    throw new Error('요청을 처리하지 못했어요. 연결 상태를 확인해 주세요.');
  }
  return response.json();
}
