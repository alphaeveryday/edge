export type Kind = 'analyses' | 'drafts' | 'reports';
export type State = 'queued' | 'running' | 'succeeded' | 'failed';
export type Overview = { as_of: string; active_visitors: number; today_visitors: number; tracking_since: string | null; queued: number; running: number; today_jobs: Record<State, number>; daily: { day: string; visitors: number; analyses: number }[] };
export type Row = { id: string; visitor: string; at: string; state?: State; stage?: string; failure?: string; email?: string; started_at?: string; finished_at?: string };
export type Records = { items: Row[]; total: number; page: number; page_size: number };
export type Detail = { id: string; visitor: string; input: import('../api').Input; result?: Record<string, unknown> | null; state?: State; stage?: string; started_at?: string; finished_at?: string; failure?: string; events?: { at: string; stage: string }[]; followups?: { input: { question: string }; result: unknown; state: State }[] };

export function progress(row: { stage?: string; started_at?: string; finished_at?: string }, asOf?: string) {
  const stage = row.stage ? ({ question: '질문 해석', portfolio: '구성 분석', outlook: '전망 검토', opinion: '최종 의견' }[row.stage] || row.stage) : '';
  const seconds = row.started_at ? Math.max(0, Math.floor((new Date(row.finished_at || asOf || Date.now()).getTime() - new Date(row.started_at).getTime()) / 1000)) : null;
  return [stage, seconds !== null && Number.isFinite(seconds) ? `${Math.floor(seconds / 60)}분 ${seconds % 60}초` : ''].filter(Boolean).join(' · ');
}

export class ApiError extends Error { constructor(public status: number, message: string) { super(message); } }
export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch('/api/portfolio/admin' + path, { ...init, signal: init?.signal ?? AbortSignal.timeout(15000), headers: { 'Content-Type': 'application/json', ...init?.headers } });
  if (!response.ok) {
    const messages: Record<number, string> = { 401: '관리자 아이디와 비밀번호를 확인해 주세요.', 429: '로그인 시도가 많습니다. 15분 후 다시 시도해 주세요.', 503: path === '/login' ? '관리자 자격이 아직 설정되지 않았습니다.' : '서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.', 404: '기록을 찾을 수 없습니다.' };
    throw new ApiError(response.status, messages[response.status] || '불러오지 못했습니다. 연결 상태를 확인해 주세요.');
  }
  return response.json();
}
