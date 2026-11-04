import { useRef, useState } from 'react';
import { Button, TextInput } from './design-system/components';
import { accountIneligibleReason, type TaxProfile } from './accountEligibility';
import { Glyph, manInput, type GlyphName } from './flow';

// 2/3 앞으로의 계획(I3): 매달 넣을 돈 · 쓸 때 · 쓸 수 있는 계좌. 옛 넣는 방법·기간 단계와 절세 창을 한 화면으로 합쳤다.
export const monthlyOff = ['이미 다 매수한 상태에요', '아직 고민 중'];
export const horizonLabel: Record<string, string> = { '3년 안': '3년 안', '3~10년': '3~10년 뒤', '10년 이상': '10년 넘게 뒤', '정한 때 없음': '정한 때 없어요' };
const horizons: [string, string, GlyphName][] = [['3년 안', '결혼, 집처럼 곧 쓸 돈', 'clock'], ['3~10년', '아이 교육, 차 바꾸기', 'calendar'], ['10년 이상', '노후, 오래 모을 돈', 'sprout'], ['정한 때 없음', '그때그때 정해요', 'help']];
export const accountBenefit: Record<string, string> = { ISA: '번 돈 200만 원까지 세금 0원(서민형 400만)', 연금저축: '넣은 돈의 13.2~16.5% 돌려받기', IRP: '연금저축과 합쳐 900만 원까지 공제', '일반 계좌': '제한 없음' };
const blankTax: TaxProfile = { residence: '', age: '', income: '', financial_income_tax: '', previous_year_earned_income: '', irp_qualification: '', recommended_accounts: [], accounts: [] };
const isaQuestions: [string, 'residence' | 'financial_income_tax', [string, string][]][] = [
  ['국내에 살고 있나요?', 'residence', [['예', '대한민국 거주자'], ['아니요', '비거주자'], ['모르겠어요', '확인 필요']]],
  ['최근 3년 안에 금융소득종합과세를 낸 적이 있나요?', 'financial_income_tax', [['예', '예'], ['아니요', '아니요'], ['모르겠어요', '확인 필요']]]];
const irpAnswers: [string, string][] = [['직장이나 사업 소득이 있어요', '근로자·자영업자·직역연금 가입자'], ['퇴직금을 받을 예정이에요', '퇴직급여 수령 대상'], ['둘 다 아니에요', '해당 없음'], ['모르겠어요', '확인 필요']];

// 고르지 않은 답은 서버에 '확인 필요'로 보낸다(서버 계약은 빈 값을 받지 않는다). 가입할 수 없다고 답한 계좌는 전략 대상에서 뺀다.
export function taxPayload(tax: TaxProfile | null, selected: string[]): TaxProfile | null {
  const targets = selected.filter(a => a !== '일반 계좌');
  if (!targets.length) return null;
  const t = tax ?? blankTax;
  const profile = { ...t, residence: t.residence || '확인 필요', age: t.age || '확인 필요', income: t.income || '확인 필요', financial_income_tax: t.financial_income_tax || '확인 필요' };
  return { ...profile, recommended_accounts: targets.filter(a => !accountIneligibleReason(a, profile)),
    accounts: targets.map(account => t.accounts.find(a => a.account === account) ?? { account, subtype: '', opened: '', remaining_man: '', paid_man: '' }) };
}
// 가입할 수 없다고 답한 새 계좌는 쓸 계좌에서도 뺀다(서버는 고른 계좌에 추천을 넣을 수 있다). 종목이 담긴 계좌는 그대로다.
export function usableAccounts(selected: string[], held: string[], tax: TaxProfile | null) {
  const t = tax ?? blankTax, profile = { ...t, age: t.age || '확인 필요' };
  return selected.filter(a => held.includes(a) || !accountIneligibleReason(a, profile));
}
export function planGuidance({ how, contribution, horizon, selected, tax, held }: { how: string; contribution: string; horizon: string; selected: string[]; tax: TaxProfile | null; held: string[] }) {
  if (!how || !monthlyOff.includes(how) && how !== '매달 꾸준히') return '매달 넣을 돈을 골라 주세요';
  if (how === '매달 꾸준히' && !(Number(contribution) > 0)) return '0보다 큰 금액을 적어 주세요';
  if (how === '매달 꾸준히' && Number(contribution) * 1e4 > 1e13) return '매달 넣을 돈은 10조 원 이하로 적어 주세요';
  if (!horizon) return '언제쯤 쓸지 골라 주세요';
  if (!usableAccounts(selected, held, tax).length) return '쓸 계좌를 하나 이상 골라 주세요';
  // 확인 질문은 실제로 쓸 새 계좌만 묻는다 — 가입할 수 없다고 답해 빠진 계좌의 남은 질문으로 막지 않는다.
  const t = tax ?? blankTax, usable = usableAccounts(selected, held, tax);
  if (usable.includes('ISA') && !held.includes('ISA') && (!t.residence || !t.financial_income_tax)) return 'ISA 확인 질문에 답해 주세요';
  if (usable.includes('IRP') && !held.includes('IRP') && !t.irp_qualification) return 'IRP 가입 조건을 골라 주세요';
  return '';
}

export function PlanInput({ how, setHow, contribution, setContribution, horizon, setHorizon, selected, setSelected, held, tax, setTax, starting }: {
  how: string; setHow: (v: string) => void; contribution: string; setContribution: (v: string) => void; horizon: string; setHorizon: (v: string) => void;
  selected: string[]; setSelected: (v: string[]) => void; held: string[]; tax: TaxProfile | null; setTax: (v: TaxProfile) => void; starting: boolean;
}) {
  const amount = useRef<HTMLInputElement>(null);
  const monthly = how === '매달 꾸준히';
  const [custom, setCustom] = useState(() => monthly && ![10, 30, 50, 100].includes(Number(contribution)));
  const none = starting ? ['아직 고민 중', '아직 몰라요'] : ['이미 다 매수한 상태에요', '안 넣어요'];
  const t = tax ?? blankTax;
  const profile = { ...t, age: t.age || '확인 필요' };
  const reason = (account: string) => accountIneligibleReason(account, profile);
  // 가입할 수 없다고 답한 계좌는 체크가 풀리고 보내지 않는다(usableAccounts). 답을 고치면 고른 그대로 돌아온다.
  const answer = (key: 'residence' | 'financial_income_tax' | 'irp_qualification', value: string) => setTax({ ...t, [key]: value });
  const setMonthly = (value: number) => { setHow('매달 꾸준히'); setContribution(String(Math.max(0, Math.round(value * 1e4) / 1e4))); };
  const segment = (label: string, current: string, options: [string, string][], change: (v: string) => void, list = false) => <div className="fl-question" role="group" aria-label={label}>
    <p>{label}</p><div className={list ? 'fl-options' : 'fl-seg'}>{options.map(([text, value]) => <Button key={value} className={list ? 'fl-option' : 'fl-seg__item'} aria-pressed={current === value} onClick={() => change(value)}>{text}{list && <i className="fl-radio" aria-hidden="true" />}</Button>)}</div></div>;
  const blocked = ['ISA', 'IRP', '연금저축'].some(a => reason(a) && !held.includes(a));
  return <>
    <section className="fl-card" aria-labelledby="monthly-title">
      <h2 className="fl-card__title" id="monthly-title">{starting ? '매달 넣을 돈' : '매달 더 넣을 돈'}</h2>
      <div className="fl-stepper">
        <Button className="fl-round" aria-label="10만 원 줄이기" disabled={!monthly || !(Number(contribution) > 0)} onClick={() => setMonthly(Number(contribution) - 10)}><Glyph name="minus" size={24} /></Button>
        <label className="fl-big" data-invalid={monthly && !(Number(contribution) > 0)}><TextInput ref={amount} className="fl-bare" aria-label="매달 넣을 돈(만 원)" inputMode="decimal" autoComplete="off" placeholder="0" value={monthly ? contribution : ''}
          onChange={e => { setHow('매달 꾸준히'); setContribution(manInput(e.target.value)); }} /><span>만 원</span></label>
        <Button className="fl-round" aria-label="10만 원 늘리기" onClick={() => setMonthly((monthly ? Number(contribution) : 0) + 10)}><Glyph name="plus" size={24} /></Button>
      </div>
      {monthly && !(Number(contribution) > 0) && <p className="fl-error">0보다 큰 금액을 적어 주세요</p>}
      <div className="fl-chips" role="group" aria-labelledby="monthly-title">
        {[10, 30, 50, 100].map(v => { const on = monthly && !custom && contribution === String(v); return <Button key={v} className="fl-chip" aria-pressed={on} onClick={() => { setCustom(false); setMonthly(v); }}>{on && <Glyph name="check" size={16} />}{v}만</Button>; })}
        <Button className="fl-chip" aria-pressed={monthly && custom} onClick={() => { setCustom(true); setHow('매달 꾸준히'); amount.current?.focus(); }}>{monthly && custom && <Glyph name="check" size={16} />}직접</Button>
        <Button className="fl-chip" aria-pressed={monthlyOff.includes(how)} onClick={() => { setCustom(false); setHow(none[0]); }}>{monthlyOff.includes(how) && <Glyph name="check" size={16} />}{none[1]}</Button>
      </div>
    </section>
    <section className="fl-card" aria-labelledby="when-title">
      <h2 className="fl-card__title" id="when-title">이 돈은 언제 쓰나요?</h2>
      <div className="fl-list" role="group" aria-labelledby="when-title">{horizons.map(([value, sub, icon]) => <Button key={value} className="fl-when" aria-pressed={horizon === value} onClick={() => setHorizon(value)}>
        <span className="fl-icontile"><Glyph name={icon} /></span><span className="fl-when__text"><b>{horizonLabel[value]}</b><small>{sub}</small></span><i className="fl-radio" aria-hidden="true" /></Button>)}</div>
    </section>
    <section className="fl-card" aria-labelledby="acct-title">
      <h2 className="fl-card__title" id="acct-title">쓸 수 있는 계좌</h2>
      <p className="fl-card__desc" id="acct-desc">{blocked ? '가입할 수 없는 계좌는 고를 수 없어요. 까닭은 계좌 아래에 적었어요.' : '1단계에서 담은 계좌는 골라 뒀어요. 아직 없는 계좌도 고르면 만들지 알려 드려요.'}</p>
      <div className="fl-tiles" role="group" aria-labelledby="acct-title">{['ISA', '연금저축', 'IRP', '일반 계좌'].map(account => {
        const why = held.includes(account) ? '' : reason(account), on = selected.includes(account) && !why;
        // 마지막 남은 계좌는 해제하지 못한다 — 계좌가 빈 초안은 옛 번들(롤백)이 통째로 버린다.
        return <Button key={account} className="fl-tile" aria-pressed={on} aria-describedby={why ? 'why-' + account : held.includes(account) ? 'acct-desc' : undefined} disabled={!!why || held.includes(account) || on && selected.length === 1}
          onClick={() => setSelected(on ? selected.filter(a => a !== account) : [...selected, account])}>
          <b>{account}</b><span>{accountBenefit[account]}</span>{why && <em id={'why-' + account}>{why}</em>}<i className="fl-check" aria-hidden="true">{on && <Glyph name="check" size={16} />}</i></Button>;
      })}</div>
      {(selected.includes('ISA') && !held.includes('ISA') || reason('ISA') && !held.includes('ISA')) && <div className="fl-subcard"><h3>ISA는 두 가지만 확인할게요</h3>
        {isaQuestions.map(([label, key, options]) => <div key={key}>{segment(label, t[key], options, v => answer(key, v))}</div>)}</div>}
      {(selected.includes('IRP') && !held.includes('IRP') || reason('IRP') && !held.includes('IRP')) && <div className="fl-subcard"><h3>IRP 가입 조건을 확인할게요</h3>
        {segment('IRP 가입 조건', t.irp_qualification || '', irpAnswers, v => answer('irp_qualification', v), true)}</div>}
    </section>
  </>;
}
