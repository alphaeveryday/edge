import type { Chip, Holding, Input } from './api';
import type { TaxProfile } from './accountEligibility';
import { accounts } from './workbook';
import { preview } from './preview';

// v2 drafts hold won; v1 held 만 원. A new key keeps a rolled-back bundle from reading won as 만 원.
export const inputDraftKey = preview ? 'edge-preview-form-v2' : 'edge-portfolio-form-v2';
export const legacyDraftKey = preview ? 'edge-preview-form-v1' : 'edge-portfolio-form-v1';
export type InputDraft = { flow?: 3; total?: string; chips?: Chip[]; submittedJobId?: string; dividendGoal?: string; step: number; holdings: Holding[]; alternate: Holding[]; selected: string[]; question: string; horizon: string; how: string; interests: string[]; note: string; contribution: string; keyword: string; tax: TaxProfile | null; draftId: string; mode: 'amount' | 'percent'; fresh: boolean };
export function inputDraftFromInput(input: Input) {
  const mode = input.input_mode === 'percent' ? 'percent' : 'amount';
  const funding = input.preferences?.contribution || '';
  // 숫자 칸(monthly_krw, 원)이 있으면 그것이 먼저다(서버도 글보다 먼저 쓴다). 없으면 옛 글에서 읽는다.
  const monthly = input.monthly_krw ? [funding, String(input.monthly_krw / 1e4)] : /^매달 꾸준히 ([\d.]+)만 원$/.exec(funding);
  const draft: InputDraft = { flow: 3, step: 0, holdings: input.holdings.map(h => ({ ...h, amount_krw: h.amount_krw == null ? null : String(Number(h.amount_krw)) })),
    alternate: [], selected: [...new Set([...(input.preferences?.accounts || []), ...input.holdings.map(h => h.account)])],
    question: input.question, horizon: input.horizon, how: monthly ? '매달 꾸준히' : funding, contribution: monthly?.[1] || '',
    interests: input.preferences?.interests || [], note: input.goal_text || input.preferences?.note || '', chips: input.constraints ?? [], dividendGoal: input.preferences?.dividend_goal || '', keyword: '', tax: input.preferences?.tax_profile || null,
    draftId: crypto.randomUUID(), mode, fresh: !!input.planning };
  if (!draft.selected.length) draft.selected = ['일반 계좌'];
  return draft;
}
export function restoreInputDraft(input: Input) {
  sessionStorage.setItem(inputDraftKey, JSON.stringify(inputDraftFromInput(input)));
}
export function readInputDraft(): InputDraft | null {
  try {
    const current = sessionStorage.getItem(inputDraftKey), legacy = current === null;
    const d = JSON.parse((legacy ? sessionStorage.getItem(legacyDraftKey) : current) || 'null');
    const strings = (a: unknown): a is string[] => Array.isArray(a) && a.every(v => typeof v === 'string');
    const holdings = (a: unknown) => Array.isArray(a) && a.length <= 20 && a.every(h => h && typeof h.name === 'string' && typeof h.account === 'string' && typeof h.instrument_id === 'string' && (h.amount_krw === null || typeof h.amount_krw === 'string'));
    if (!d || !Number.isInteger(d.step) || d.step < 0 || d.step > 4 || !holdings(d.holdings) || !holdings(d.alternate) || !strings(d.selected) || !strings(d.interests)) return null;
    if (!d.selected.length || new Set(d.selected).size !== d.selected.length || !d.selected.every((a: string) => accounts.includes(a)) || ![...d.holdings, ...d.alternate].every((h: Holding) => d.selected.includes(h.account))) return null;
    if (!['question', 'horizon', 'how', 'note', 'contribution', 'keyword', 'draftId'].every(k => typeof d[k] === 'string') || !['amount', 'percent'].includes(d.mode) || typeof d.fresh !== 'boolean') return null;
    if (d.dividendGoal !== undefined && (typeof d.dividendGoal !== 'string' || d.dividendGoal.length > 100)) return null;
    const chipKinds = ['no_leverage', 'some_bonds', 'dividend', 'max_positions', 'no_sell', 'kr_listed_etf_only'];
    if (d.chips !== undefined && (!Array.isArray(d.chips) || d.chips.length > 6 || !d.chips.every((c: Chip) => c && chipKinds.includes(c.kind) && (c.value === undefined || Number.isInteger(c.value))))) return null;
    if (d.total !== undefined && (typeof d.total !== 'string' || !/^\d{0,15}(\.\d{0,4})?$/.test(d.total))) return null; // InputForm 전체 금액 칸이 만드는 형태(크기는 화면이 막는다)
    // 옛 5단계(보유·넣는 방법·기간·관심사·질문)를 3단계(가진 것·계획·궁금한 점)로 옮긴다.
    if (d.flow !== 3) { d.step = [0, 1, 1, 2, 2][d.step]; d.flow = 3; }
    const t = d.tax;
    if (t !== null && (!t || !['residence', 'age', 'income', 'financial_income_tax'].every(k => typeof t[k] === 'string') || !strings(t.recommended_accounts) || !Array.isArray(t.accounts) || !t.accounts.every((a: Record<string, unknown>) => a && ['account', 'subtype', 'opened', 'remaining_man', 'paid_man'].every(k => typeof a[k] === 'string')))) return null;
    if (legacy) {
      // Only the amount side was in 만 원 (holdings in amount mode, the kept amounts in percent mode).
      const won = (rows: Holding[]) => rows.map(h => ({ ...h, amount_krw: h.amount_krw ? String(Math.round(Number(h.amount_krw) * 10000)) : h.amount_krw }));
      if (d.mode === 'amount') d.holdings = won(d.holdings); else d.alternate = won(d.alternate);
      // Migrate once so clearing the v2 draft cannot bring the v1 draft back.
      try { sessionStorage.removeItem(legacyDraftKey); sessionStorage.setItem(inputDraftKey, JSON.stringify(d)); } catch { /* The converted draft still opens in this tab. */ }
    }
    return d;
  } catch { return null; }
}
