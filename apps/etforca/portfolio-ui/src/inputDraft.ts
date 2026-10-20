import type { Holding, Input } from './api';
import type { TaxProfile } from './TaxAccounts';
import { accounts } from './workbook';
import { preview } from './preview';

export const inputDraftKey = preview ? 'edge-preview-form-v1' : 'edge-portfolio-form-v1';
export type InputDraft = { submittedJobId?: string; dividendGoal?: string; step: number; holdings: Holding[]; alternate: Holding[]; selected: string[]; question: string; horizon: string; how: string; interests: string[]; note: string; contribution: string; keyword: string; tax: TaxProfile | null; draftId: string; mode: 'amount' | 'percent'; fresh: boolean };
export function inputDraftFromInput(input: Input) {
  const mode = input.input_mode === 'percent' ? 'percent' : 'amount';
  const funding = input.preferences?.contribution || '';
  const monthly = /^매달 꾸준히 ([\d.]+)만 원$/.exec(funding);
  const draft: InputDraft = { step: 0, holdings: input.holdings.map(h => ({ ...h, amount_krw: h.amount_krw == null ? null : String(mode === 'amount' ? Number((Number(h.amount_krw) / 10000).toFixed(4)) : Number(h.amount_krw)) })),
    alternate: [], selected: [...new Set([...(input.preferences?.accounts || []), ...input.holdings.map(h => h.account)])],
    question: input.question, horizon: input.horizon, how: monthly ? '매달 꾸준히' : funding, contribution: monthly?.[1] || '',
    interests: input.preferences?.interests || [], note: input.preferences?.note || '', dividendGoal: input.preferences?.dividend_goal || '', keyword: '', tax: input.preferences?.tax_profile || null,
    draftId: crypto.randomUUID(), mode, fresh: !!input.planning };
  if (!draft.selected.length) draft.selected = ['일반 계좌'];
  return draft;
}
export function restoreInputDraft(input: Input) {
  sessionStorage.setItem(inputDraftKey, JSON.stringify(inputDraftFromInput(input)));
}
export function readInputDraft(): InputDraft | null {
  try {
    const d = JSON.parse(sessionStorage.getItem(inputDraftKey) || 'null');
    const strings = (a: unknown): a is string[] => Array.isArray(a) && a.every(v => typeof v === 'string');
    const holdings = (a: unknown) => Array.isArray(a) && a.length <= 20 && a.every(h => h && typeof h.name === 'string' && typeof h.account === 'string' && typeof h.instrument_id === 'string' && (h.amount_krw === null || typeof h.amount_krw === 'string'));
    if (!d || !Number.isInteger(d.step) || d.step < 0 || d.step > 4 || !holdings(d.holdings) || !holdings(d.alternate) || !strings(d.selected) || !strings(d.interests)) return null;
    if (!d.selected.length || new Set(d.selected).size !== d.selected.length || !d.selected.every((a: string) => accounts.includes(a)) || ![...d.holdings, ...d.alternate].every((h: Holding) => d.selected.includes(h.account))) return null;
    if (!['question', 'horizon', 'how', 'note', 'contribution', 'keyword', 'draftId'].every(k => typeof d[k] === 'string') || !['amount', 'percent'].includes(d.mode) || typeof d.fresh !== 'boolean') return null;
    if (d.dividendGoal !== undefined && (typeof d.dividendGoal !== 'string' || d.dividendGoal.length > 100)) return null;
    const t = d.tax;
    if (t !== null && (!t || !['residence', 'age', 'income', 'financial_income_tax'].every(k => typeof t[k] === 'string') || !strings(t.recommended_accounts) || !Array.isArray(t.accounts) || !t.accounts.every((a: Record<string, unknown>) => a && ['account', 'subtype', 'opened', 'remaining_man', 'paid_man'].every(k => typeof a[k] === 'string')))) return null;
    return d;
  } catch { return null; }
}
