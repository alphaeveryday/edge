import { useRef, useState } from 'react';

export type TaxProfile = { residence: string; age: string; income: string; financial_income_tax: string; recommended_accounts: string[]; accounts: { account: string; subtype: string; opened: string; remaining_man: string; paid_man: string }[] };
export function missingTaxAnswers(value: TaxProfile, accounts: string[]) {
  return [!value.residence && '세법상 거주지', !value.age && '나이 구간', !value.income && '연간 소득 구간', !value.financial_income_tax && '금융소득종합과세 여부', ...accounts.filter(a => a !== '일반 계좌' && !value.accounts.find(v => v.account === a)?.subtype).map(a => a + ' 세부 유형'), !value.recommended_accounts.length && '전략에 포함할 계좌'].filter(Boolean).join(', ');
}
export function TaxAccounts({ accounts, value, onSave, onPending }: { accounts: string[]; value: TaxProfile | null; onSave: (value: TaxProfile) => Promise<void>; onPending: (pending: boolean) => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [draft, setDraft] = useState<TaxProfile | null>(null);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const choices = (label: string, name: string, current: string, options: string[], change: (v: string) => void) => <fieldset className="tax-question"><legend>{label}</legend><div className="tax-options">{options.map(v => <label className="tax-option" key={v}><input type="radio" name={name} value={v} checked={current === v} onChange={() => change(v)} /><span>{v}</span></label>)}</div></fieldset>;
  const select = (label: string, key: 'residence' | 'age' | 'income' | 'financial_income_tax', options: string[]) => choices(label, key, draft![key], options, v => setDraft({ ...draft!, [key]: v }));
  return <><button className="rec tax-trigger" onClick={() => { setDraft({ residence: value?.residence || '', age: value?.age || '', income: value?.income || '', financial_income_tax: value?.financial_income_tax || '', recommended_accounts: value?.recommended_accounts || [], accounts: accounts.map(account => value?.accounts.find(a => a.account === account) || { account, subtype: '', opened: '', remaining_man: '', paid_man: '' }) }); setError(''); onPending(!value || !!missingTaxAnswers(value, accounts)); dialog.current!.showModal(); }}>최적의 ISA, IRP 등 세금 혜택과 투자전략까지 정리해드릴까요?</button>
    <dialog className="followup-dialog tax-dialog" ref={dialog} aria-labelledby="tax-title"><button className="dialog-close" aria-label="계좌 정보 닫기" onClick={() => dialog.current!.close()}>×</button><h2 id="tax-title">나에게 맞는 절세 전략</h2><p>각 질문에 답해 주세요. 모르면 ‘확인 필요’를 직접 선택해 주세요. 마지막에 세금 혜택과 투자전략을 살펴볼 계좌를 선택할 수 있어요.</p>{draft && <form onChange={() => onPending(true)} onSubmit={async e => { e.preventDefault(); const missing = missingTaxAnswers(draft, accounts); if (missing) { setError(missing + '을(를) 선택해 주세요.'); return; } setSaving(true); setError(''); try { await onSave(draft); onPending(false); dialog.current!.close(); } catch { setError('저장하지 못했어요. 다시 시도해 주세요.'); } finally { setSaving(false); } }}>
      {select('세법상 거주지', 'residence', ['대한민국 거주자', '비거주자', '확인 필요'])}
      {select('나이 구간', 'age', ['19세 미만', '19~54세', '55세 이상', '확인 필요'])}
      {select('연간 소득 구간', 'income', ['근로소득만 · 총급여 5,500만 원 이하', '근로소득만 · 총급여 5,500만 원 초과', '종합소득금액 4,500만 원 이하', '종합소득금액 4,500만 원 초과', '소득 없음', '확인 필요'])}
      {select('직전 3개 과세기간 중 금융소득종합과세 대상이었나요?', 'financial_income_tax', ['아니요', '예', '확인 필요'])}
      {draft.accounts.map((a, i) => { const update = (key: string, v: string) => setDraft({ ...draft, accounts: draft.accounts.map((x, n) => n === i ? { ...x, [key]: v } : x) }); return a.account === '일반 계좌' ? null : <fieldset key={a.account}><legend>{a.account}</legend>{a.account !== '일반 계좌' && <>
        {choices('계좌 세부 유형', a.account + '-subtype', a.subtype, a.account === 'ISA' ? ['중개형 · 일반형', '중개형 · 서민형', '중개형 · 농어민형', '신탁형', '일임형', '확인 필요'] : a.account === '연금저축' ? ['연금저축펀드', '연금저축보험', '연금저축신탁', '확인 필요'] : ['개인형 IRP', '확인 필요'], v => update('subtype', v))}
        <label>계좌 개설일 · 선택<input type="date" max={new Date().toLocaleDateString('en-CA')} value={a.opened} onChange={e => update('opened', e.target.value)} /></label>
        <label>금융사에서 확인한 남은 납입 한도 · 만 원<input type="number" min="0" max="1000000000" step="0.01" value={a.remaining_man} onChange={e => update('remaining_man', e.target.value)} placeholder="모르면 비워 두세요" /></label>
        <label>올해 본인이 납입한 금액 · 만 원<input type="number" min="0" max="1000000000" step="0.01" value={a.paid_man} onChange={e => update('paid_man', e.target.value)} placeholder="퇴직금 이전액은 제외해 주세요" /></label>
        <p className="notice">{a.account === 'ISA' ? 'ISA 유형·의무가입기간과 해외 상장 ETF 직접 매수 제한을 함께 검토해요.' : a.account === 'IRP' ? '위험자산 한도와 상품별 매수 가능 여부, 연금 수령 조건을 함께 검토해요.' : '계좌 유형에 따라 ETF 직접 매수가 가능한지 먼저 확인해요.'}</p></>}</fieldset>; })}
      <fieldset className="tax-targets"><legend>전략에 포함할 계좌</legend><p>여러 개를 선택할 수 있어요. 아직 없는 계좌도 선택해 주세요.</p>{['IRP', 'ISA', '연금저축'].map(account => <label className="tax-option" key={account}><input type="checkbox" checked={draft.recommended_accounts.includes(account)} onChange={e => setDraft({ ...draft, recommended_accounts: e.target.checked ? [...draft.recommended_accounts, account] : draft.recommended_accounts.filter(a => a !== account) })} /><span>{account}</span></label>)}</fieldset>
      <p className="notice">모르는 정보는 ‘확인 필요’로 남겨 주세요. 계좌 선택만으로 가입 자격이나 세금 혜택이 확정되지는 않아요.</p><button className="primary" disabled={saving}>{saving ? '저장 중…' : '선택한 내용 적용하기'}</button>{error && <p role="alert">{error}</p>}
    </form>}</dialog></>;
}
