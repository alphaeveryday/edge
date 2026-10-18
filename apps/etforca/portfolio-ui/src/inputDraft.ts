import type { Holding } from './api';
import type { TaxProfile } from './TaxAccounts';
import { accounts } from './workbook';
import { preview } from './preview';

export const inputDraftKey = preview ? 'edge-preview-form-v1' : 'edge-portfolio-form-v1';
export type InputDraft = { submittedJobId?: string; step: number; holdings: Holding[]; alternate: Holding[]; selected: string[]; question: string; horizon: string; how: string; interests: string[]; note: string; contribution: string; keyword: string; tax: TaxProfile | null; draftId: string; mode: 'amount' | 'percent'; fresh: boolean };
export function readInputDraft(): InputDraft | null {
  try {
    const d = JSON.parse(sessionStorage.getItem(inputDraftKey) || 'null');
    const strings = (a: unknown): a is string[] => Array.isArray(a) && a.every(v => typeof v === 'string');
    const holdings = (a: unknown) => Array.isArray(a) && a.length <= 20 && a.every(h => h && typeof h.name === 'string' && typeof h.account === 'string' && typeof h.instrument_id === 'string' && (h.amount_krw === null || typeof h.amount_krw === 'string'));
    if (!d || !Number.isInteger(d.step) || d.step < 0 || d.step > 4 || !holdings(d.holdings) || !holdings(d.alternate) || !strings(d.selected) || !strings(d.interests)) return null;
    if (!d.selected.length || new Set(d.selected).size !== d.selected.length || !d.selected.every((a: string) => accounts.includes(a)) || ![...d.holdings, ...d.alternate].every((h: Holding) => d.selected.includes(h.account))) return null;
    if (!['question', 'horizon', 'how', 'note', 'contribution', 'keyword', 'draftId'].every(k => typeof d[k] === 'string') || !['amount', 'percent'].includes(d.mode) || typeof d.fresh !== 'boolean') return null;
    const t = d.tax;
    if (t !== null && (!t || !['residence', 'age', 'income', 'financial_income_tax'].every(k => typeof t[k] === 'string') || !strings(t.recommended_accounts) || !Array.isArray(t.accounts) || !t.accounts.every((a: Record<string, unknown>) => a && ['account', 'subtype', 'opened', 'remaining_man', 'paid_man'].every(k => typeof a[k] === 'string')))) return null;
    return d;
  } catch { return null; }
}
