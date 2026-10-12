export const roles = ['question', 'portfolio', 'outlook', 'opinion'] as const;
export type Role = typeof roles[number];
export const titles: Record<Role, string> = { question: '질문에 대한 답', portfolio: '현재 포트폴리오 분석', outlook: '앞으로의 전망', opinion: '투자 의견' };
export type Holding = { name: string; account: string; amount_krw: string };
export type Input = { holdings: Holding[]; question: string; horizon: string };
export type Section = { paragraphs: string[]; evidence: { source_id: string; quote: string }[]; limitations: string[] };
export type Report = {
  sections: Record<Role, Section>;
  metrics: { holdings: { name: string; account: string; weight_pct: number }[]; total_krw: string; unavailable_reason: string };
  sources: { id: string; url: string; fetched_at: string }[];
  model: string; as_of: string; elapsed_seconds: number;
};
export type Job = { id: string; state: 'queued' | 'running' | 'succeeded' | 'failed'; stage: Role | null; result: Report | null; failure: string | null; started_at: string | null };

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch('/api/portfolio' + path, { ...init, signal: AbortSignal.timeout(15000), headers: { 'Content-Type': 'application/json', ...init?.headers } });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    if (body.detail === 'CHAT_LIMIT') throw new Error('CHAT_LIMIT');
    if (response.status === 429) throw new Error('현재 요청이 많거나 오늘 체험 횟수를 모두 사용했어요. 잠시 후 다시 시도해 주세요.');
    if (response.status === 401) throw new Error('분석을 시작한 브라우저에서 다시 열어 주세요.');
    throw new Error('요청을 처리하지 못했어요. 연결 상태를 확인해 주세요.');
  }
  return response.json();
}
