import { useRef, useState } from 'react';

export type TaxProfile = { residence: string; age: string; income: string; financial_income_tax: string; accounts: { account: string; subtype: string; opened: string; remaining_man: string; paid_man: string }[] };
export function TaxAccounts({ accounts, value, onSave }: { accounts: string[]; value: TaxProfile | null; onSave: (value: TaxProfile) => Promise<void> }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [draft, setDraft] = useState<TaxProfile | null>(null);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const select = (label: string, key: 'residence' | 'age' | 'income' | 'financial_income_tax', options: string[]) => <label>{label}<select required value={draft![key]} onChange={e => setDraft({ ...draft!, [key]: e.target.value })}><option value="">선택해 주세요</option>{options.map(v => <option key={v}>{v}</option>)}</select></label>;
  return <><button className="rec tax-trigger" onClick={() => { setDraft({ residence: value?.residence || '', age: value?.age || '', income: value?.income || '', financial_income_tax: value?.financial_income_tax || '', accounts: accounts.map(account => value?.accounts.find(a => a.account === account) || { account, subtype: '', opened: '', remaining_man: '', paid_man: '' }) }); setError(''); dialog.current!.showModal(); }}>계좌별로 세금을 고려해 포트폴리오를 추천해드릴까요?</button>
    <dialog className="followup-dialog tax-dialog" ref={dialog} aria-labelledby="tax-title"><button className="dialog-close" aria-label="계좌 정보 닫기" onClick={() => dialog.current!.close()}>×</button><h2 id="tax-title">계좌와 세금 정보</h2><p>계좌의 종류와 납입 여유를 확인해요. 모르는 항목은 확인 필요로 남겨도 돼요.</p>{draft && <form onSubmit={async e => { e.preventDefault(); setSaving(true); setError(''); try { await onSave(draft); dialog.current!.close(); } catch { setError('저장하지 못했어요. 다시 시도해 주세요.'); } finally { setSaving(false); } }}>
      {select('세법상 거주지', 'residence', ['대한민국 거주자', '비거주자', '확인 필요'])}
      {select('나이 구간', 'age', ['19세 미만', '19~54세', '55세 이상', '확인 필요'])}
      {select('연간 소득 구간', 'income', ['근로소득만 · 총급여 5,500만 원 이하', '근로소득만 · 총급여 5,500만 원 초과', '종합소득금액 4,500만 원 이하', '종합소득금액 4,500만 원 초과', '소득 없음', '확인 필요'])}
      {select('직전 3개 과세기간 중 금융소득종합과세 대상이었나요?', 'financial_income_tax', ['아니요', '예', '확인 필요'])}
      {draft.accounts.map((a, i) => { const update = (key: string, v: string) => setDraft({ ...draft, accounts: draft.accounts.map((x, n) => n === i ? { ...x, [key]: v } : x) }); return <fieldset key={a.account}><legend>{a.account}</legend>{a.account !== '일반 계좌' && <>
        <label>계좌 세부 유형<select required value={a.subtype} onChange={e => update('subtype', e.target.value)}><option value="">선택해 주세요</option>{(a.account === 'ISA' ? ['중개형 · 일반형', '중개형 · 서민형', '중개형 · 농어민형', '신탁형', '일임형', '확인 필요'] : a.account === '연금저축' ? ['연금저축펀드', '연금저축보험', '연금저축신탁', '확인 필요'] : ['개인형 IRP', '확인 필요']).map(v => <option key={v}>{v}</option>)}</select></label>
        <label>계좌 개설일 · 선택<input type="date" max={new Date().toLocaleDateString('en-CA')} value={a.opened} onChange={e => update('opened', e.target.value)} /></label>
        <label>금융사에서 확인한 남은 납입 한도 · 만 원<input type="number" min="0" max="1000000000" step="0.01" value={a.remaining_man} onChange={e => update('remaining_man', e.target.value)} placeholder="모르면 비워 두세요" /></label>
        <label>올해 본인이 납입한 금액 · 만 원<input type="number" min="0" max="1000000000" step="0.01" value={a.paid_man} onChange={e => update('paid_man', e.target.value)} placeholder="퇴직금 이전액은 제외해 주세요" /></label>
        <p className="notice">{a.account === 'ISA' ? 'ISA 유형·의무가입기간과 해외 상장 ETF 직접 매수 제한을 함께 검토해요.' : a.account === 'IRP' ? '위험자산 한도와 상품별 매수 가능 여부, 연금 수령 조건을 함께 검토해요.' : '계좌 유형에 따라 ETF 직접 매수가 가능한지 먼저 확인해요.'}</p></>}</fieldset>; })}
      <p className="notice">입력한 내용은 분석용으로 저장돼요. 확인 필요 항목은 세금 혜택이 확정된 것으로 계산하지 않아요.</p><button className="primary" disabled={saving}>{saving ? '저장 중…' : '계좌 정보 적용하기'}</button>{error && <p role="alert">{error}</p>}
    </form>}</dialog></>;
}
