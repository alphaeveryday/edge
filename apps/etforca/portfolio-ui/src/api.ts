import { preview, previewRequest } from './preview';
export const roles = ['question', 'portfolio', 'outlook', 'opinion'] as const;
export type Role = typeof roles[number];
export const titles: Record<Role, string> = { question: '질문에 대한 답', portfolio: '현재 포트폴리오 분석', outlook: '앞으로의 전망', opinion: '투자 의견' };
export type Holding = { name: string; account: string; amount_krw: string | null; kind?: 'cash' | 'etf'; instrument_id?: string };
export type Instrument = { id: string; market: string; ticker: string; code: string; name: string; kind: 'etf' | 'cash' };
export type Catalog = { fetched_at: string; counts: { KR: number; US: number }; instruments: Instrument[] };
export type Input = { draft_id?: string; holdings: Holding[]; question: string; horizon: string; input_mode?: string; planning?: boolean; preferences?: { accounts: string[]; contribution: string; interests: string[]; note: string; tax_profile: import('./TaxAccounts').TaxProfile | null } };
export type Section = { paragraphs: string[]; evidence: { source_id: string; quote: string }[]; limitations: string[] };
export type Evaluation = { scores: { leadership: number; profitability: number; stability: number } | null; overall: number | null; reason: Section; method: string; rubric_version?: number };
export type Allocation = { instrument_id: string; name: string; ticker: string; kind: 'cash' | 'etf'; account: string; weight_pct: number; amount_krw: string | null };
export type Execution = { status: 'ready' | 'conditional' | 'needs_input' | 'infeasible'; reasons: string[]; assumptions: string[]; additional_krw: string; monthly_krw: string | null; required_additional_krw: string | null; months: number | null; rows: { instrument_id: string; name?: string; account: string; before_krw: string; target_krw: string; buy_krw: string; sell_krw: string; cash_used_krw: string }[]; accounts: { account: string; deposit_krw: string; withdrawal_krw: string }[] };
export type History = { points: { date: string; recommended: number; current: number | null }[]; basis: string; source_url: string; baseline_kind?: 'new_cash' | 'current_holdings' | 'unknown_weights'; current_unavailable_reason?: string; interval?: '1d'; currency?: 'KRW'; initial_krw?: number; method?: string; sources?: { instrument_id: string; url: string }[] };
export type Recommendation = { title: string; execution?: Execution; allocations: Allocation[]; evaluation: Evaluation; total_krw: string | null; actions: { instrument_id: string; name: string; ticker: string; account: string; before_pct: number | null; after_pct: number; action: string; buy_krw?: string; sell_krw?: string; cash_used_krw?: string }[]; expected_return_1y: number | null; dividend_yield_1y: number | null; historical_performance: History | null; unavailable_reason: string; target_return_1y?: number; dividend_basis?: string; return_forecast?: { reason: string; method: string }; dividend_observations?: { instrument_id: string; as_of: string; trailing_yield_pct: number; source_url?: string; method?: string; evidence: { url: string; quote: string }[] }[] };
export type Report = {
  partial_report?: Report;
  completion?: { status: 'complete' | 'partial' | 'needs_input' | 'no_match'; tasks: { question: string; status: string; answer: string; missing: string[] }[]; missing_inputs: string[]; missing_research: { instrument_id: string; field: string; status: string }[] };
  scenarios?: import('./ScenarioTable').Scenarios;
  execution?: Execution;
  schema_version?: number;
  current_evaluation?: Evaluation;
  recommendation?: Recommendation | null;
  optimization?: { status: 'available' | 'no_match' | 'unavailable' | 'not_requested'; selected_candidate_id: string | null; reason?: string };
  insights?: { title: string; content: Section }[];
  sections: Record<Role, Section>;
  metrics: { holdings: { name: string; account: string; weight_pct: number | null }[]; total_krw: string | null; unavailable_reason: string };
  sources: { id: string; url: string; fetched_at: string }[];
  model: string; as_of: string; elapsed_seconds: number;
};
export type Job = { id: string; state: 'queued' | 'running' | 'succeeded' | 'failed'; stage: Role | null; result: Report | null; failure: string | null; started_at: string | null };

let visitorSession: Promise<unknown> | null = null;
export function ensureVisitorSession() {
  visitorSession ??= (navigator.locks
    ? navigator.locks.request('orca-portfolio-visitor', () => request('/session'))
    : request('/session')).finally(() => { visitorSession = null; });
  return visitorSession;
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  if (preview) return previewRequest<T>(path, init);
  const response = await fetch('/api/portfolio' + path, { ...init, signal: AbortSignal.timeout(15000), headers: { 'Content-Type': 'application/json', ...init?.headers } });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    if (body.detail === 'WORKBOOK_CONFLICT') throw new Error('다른 탭에서 수정했어요. 저장된 내용을 다시 불러와 주세요.');
    if (body.detail === 'BACKTEST_BUSY') throw new Error('이전 백테스트를 계산하고 있어요. 완료 후 다시 조회해 주세요.');
    if (path === '/backtests' && response.status === 422) throw new Error('비중 합계가 100%인지, 조회 기간이 어제 이전인지 확인해 주세요.');
    if (path === '/workbook' && response.status === 422) throw new Error('종목·배당·월 기록의 입력값과 최대 기록 수를 확인해 주세요.');
    if (body.detail === 'CHAT_LIMIT') throw new Error('CHAT_LIMIT');
    if (body.detail === 'RECOVERY_LIMIT') throw new Error('RECOVERY_LIMIT');
    if (body.detail === 'NO_HOLDINGS') throw new Error('보유 ETF를 입력한 분석에서 뉴스레터를 신청해 주세요.');
    if (body.detail === 'SUBSCRIPTION_LIMIT') throw new Error('오늘 뉴스레터 신청 횟수를 모두 사용했어요. 내일 다시 신청해 주세요.');
    if (body.detail === 'INVALID_CONFIRMATION') throw new Error('확인 링크가 만료됐거나 이미 사용됐어요. 분석 결과에서 다시 신청해 주세요.');
    if (response.status === 422) {
      if (path.endsWith('/newsletter')) throw new Error('이메일 주소를 확인해 주세요.');
      if (path === '/newsletter/confirm') throw new Error('확인 링크가 올바르지 않아요. 분석 결과에서 다시 신청해 주세요.');
      const messages: Record<string, string> = {
        HOLDING_WEIGHTS_REQUIRED: '보유 종목의 금액 또는 비율을 입력해 주세요.',
        CONTRIBUTION_CHOICE_REQUIRED: '투자금을 넣는 방법을 선택해 주세요.',
        MONTHLY_AMOUNT_REQUIRED: '매달 투자할 금액을 입력해 주세요.',
      };
      throw new Error(messages[body.detail] || '목록에서 ETF 또는 현금을 선택하고 금액을 확인해 주세요.');
    }
    if (response.status === 429) throw new Error('현재 요청이 많거나 오늘 체험 횟수를 모두 사용했어요. 잠시 후 다시 시도해 주세요.');
    if (response.status === 401) throw new Error('분석을 시작한 브라우저에서 다시 열어 주세요.');
    throw new Error('요청을 처리하지 못했어요. 연결 상태를 확인해 주세요.');
  }
  return response.json();
}
