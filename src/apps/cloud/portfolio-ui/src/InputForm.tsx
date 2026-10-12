import { useState, type FormEvent } from 'react';
import type { Holding, Input } from './api';

const accounts = ['일반 계좌', 'ISA', '연금저축', 'IRP'];
const blank = (): Holding => ({ name: '', account: accounts[0], amount_krw: '' });
export function InputForm({ onSubmit, busy }: { onSubmit: (input: Input) => void; busy: boolean }) {
  const [holdings, setHoldings] = useState<Holding[]>([blank()]);
  const [question, setQuestion] = useState('');
  const [horizon, setHorizon] = useState('10년 이상');
  function update(index: number, key: keyof Holding, value: string) {
    setHoldings(current => current.map((h, i) => i === index ? { ...h, [key]: value } : h));
  }
  function submit(event: FormEvent) { event.preventDefault(); onSubmit({ holdings, question, horizon }); }
  return <form onSubmit={submit}>
    <span className="eyebrow">내 구성 확인</span><h1>어떤 <em>ETF</em>를<br />담았나요?</h1>
    <p className="intro">내가 가진 ETF와 궁금한 점을 알려 주세요.<br />자료를 확인하고 투자 판단을 함께 정리해 드려요.</p>
    <fieldset disabled={busy}>
      <legend className="sr-only">포트폴리오 입력</legend>
      {holdings.map((holding, index) => <article className="card holding" key={index}>
        <div className="row"><label>투자 계좌<select aria-label={`투자 계좌 ${index + 1}`} value={holding.account} onChange={e => update(index, 'account', e.target.value)}>{accounts.map(a => <option key={a}>{a}</option>)}</select></label>
          {holdings.length > 1 && <button type="button" className="plain" aria-label={`ETF ${index + 1} 삭제`} onClick={() => setHoldings(current => current.filter((_, i) => i !== index))}>×</button>}</div>
        <label>ETF 이름 또는 종목 코드<input required maxLength={100} placeholder="예: KODEX 200 또는 VOO" value={holding.name} onChange={e => update(index, 'name', e.target.value)} /></label>
        <label>현재 평가금액 <span className="muted">원화 기준</span><input required type="number" min="1" max="10000000000000" step="1" inputMode="numeric" placeholder="예: 1000000" value={holding.amount_krw} onChange={e => update(index, 'amount_krw', e.target.value)} /></label>
      </article>)}
      <button type="button" className="secondary" disabled={holdings.length >= 20} onClick={() => setHoldings([...holdings, blank()])}>＋ ETF 추가하기</button>
      <label className="standalone">이 돈은 언제 쓸 예정인가요?<select value={horizon} onChange={e => setHorizon(e.target.value)}>{['3년 안', '3~10년', '10년 이상', '정한 때 없음'].map(h => <option key={h}>{h}</option>)}</select></label>
      <label className="standalone">가장 궁금한 점<textarea maxLength={1000} rows={4} value={question} onChange={e => setQuestion(e.target.value)} placeholder="예: 두 ETF가 많이 겹치나요? 하나를 줄이는 게 좋을까요?" /></label>
      <p className="notice">자료 조사와 검토에 약 5분이 걸려요. 진행 상황과 결과는 이 브라우저에서 다시 확인할 수 있어요.</p>
      <button className="primary" type="submit">{busy ? '분석 접수 중…' : '내 포트폴리오 분석하기'}</button>
    </fieldset>
  </form>;
}
