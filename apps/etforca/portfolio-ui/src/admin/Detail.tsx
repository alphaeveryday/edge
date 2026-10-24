import { Button } from '../design-system/components';
import { useState } from 'react';
import { CustomerReport } from './CustomerReport';
import { Sources } from './Sources';
import type { Detail as Data } from './api';
import { progress } from './api';

const labels: Record<string, string> = { residence: '세법상 거주지', age: '나이 구간', income: '연간 소득 구간', financial_income_tax: '금융소득종합과세 대상', recommended_accounts: '전략에 포함할 계좌', accounts: '계좌', account: '계좌', subtype: '세부 유형', opened: '개설 시기', remaining_man: '납입 여유 · 만 원', paid_man: '올해 납입액 · 만 원', title: '제목', summary: '요약', bullets: '핵심 내용', limitations: '확인할 점', question: '질문', answer: '답변', text: '내용', body: '내용' };
export function Readable({ value }: { value: unknown }) {
  if (value === null || value === undefined || value === '') return <span className="muted">미입력</span>;
  if (Array.isArray(value)) return value.length ? <ul>{value.map((item, i) => <li key={i}><Readable value={item} /></li>)}</ul> : <span className="muted">없음</span>;
  if (typeof value === 'object') return <dl className="fields">{Object.entries(value).map(([key, item]) => <div key={key}><dt>{labels[key] || key}</dt><dd><Readable value={item} /></dd></div>)}</dl>;
  return <span>{String(value)}</span>;
}

export function Detail({ data, onClose }: { data: Data; onClose: () => void }) {
  const input = data.input;
  const p = input.preferences;
  const result = data.result;
  const [tab, setTab] = useState(result ? 'result' : 'input');
  return <section className="detail" aria-label="입력 상세">
    <header><div><span className="eyebrow">방문자 {data.visitor}</span><h2>요청 상세</h2></div><Button className="quiet" onClick={onClose} aria-label="상세 닫기">닫기 ×</Button></header>
    {data.test_label && <p className="badge test">테스트 · {data.test_label}</p>}
    {data.state && <p className="muted">{progress(data)}</p>}
    {data.failure && <p className="error">실패 사유 · {data.failure}</p>}
    <div className="detail-tabs" role="tablist" aria-label="상세 구분">{[['result', '고객 결과'], ['input', '고객 입력'], ['sources', '출처와 인용']].map(([id, label]) => <Button key={id} id={'tab-' + id} role="tab" aria-selected={tab === id} aria-controls={'panel-' + id} onClick={() => setTab(id)}>{label}</Button>)}</div>
    <div role="tabpanel" id="panel-input" aria-labelledby="tab-input" hidden={tab !== 'input'}>
    <section><h3>포트폴리오</h3><p>{input.planning ? '종목부터 추천받기' : '기존 구성 분석'}</p>
      <div className="table-wrap"><table><thead><tr><th>종목</th><th>계좌</th><th>{input.input_mode === 'percent' ? '비중' : input.input_mode === 'names' ? '금액' : '금액 · 원'}</th></tr></thead><tbody>{input.holdings.map((h, i) => <tr key={i}><td><b>{h.name}</b><small>{h.instrument_id}</small></td><td>{h.account || '일반 계좌'}</td><td>{h.amount_krw === null || h.amount_krw === undefined || input.input_mode === 'names' ? '미입력' : Number(h.amount_krw).toLocaleString('ko-KR') + (input.input_mode === 'percent' ? '%' : '')}</td></tr>)}</tbody></table></div>
      {!input.holdings.length && <p className="muted">입력한 종목이 없습니다.</p>}
    </section>
    <section><h3>투자 계획과 질문</h3><dl className="fields">{[['투자 기간', input.horizon], ['투자 방식·적립금', p?.contribution], ['선택 계좌', p?.accounts], ['관심사', p?.interests], ['질문', input.question], ['추가 고려사항', p?.note]].map(([label, value]) => <div key={String(label)}><dt>{label}</dt><dd><Readable value={value} /></dd></div>)}</dl></section>
    <section><h3>계좌·세금 정보</h3><Readable value={p?.tax_profile} /></section>
    </div>
    <div role="tabpanel" id="panel-result" aria-labelledby="tab-result" hidden={tab !== 'result'}>
      {result ? <CustomerReport result={result} /> : <p className="muted">저장된 분석 결과가 없습니다.</p>}
    </div>
    <div role="tabpanel" id="panel-sources" aria-labelledby="tab-sources" hidden={tab !== 'sources'}><Sources result={result} followups={data.followups} /></div>
    {result && <details><summary>저장된 분석 결과 전체</summary><pre>{JSON.stringify(result, null, 2)}</pre></details>}
    <section aria-label="추가 질문과 답변"><h3>추가 질문</h3>{data.followups?.length ? data.followups.map((item, i) => <article className="followup-record" key={i}>
      <h4>{item.input.question}</h4>{item.test_label && <span className="badge test">테스트 · {item.test_label}</span>}
      <p className="muted">{item.created_at ? new Date(item.created_at).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' }) + ' KST' : '저장된 시각 없음'} · {{ queued: '답변 대기', running: '답변 작성 중', succeeded: '답변 완료', failed: '답변 실패' }[item.state]}</p>
      {item.result?.answer?.paragraphs?.map((paragraph, j) => <p key={j}>{paragraph}</p>)}
      {item.result?.answer?.limitations?.map((limitation, j) => <p className="muted" key={j}>{limitation}</p>)}
      {item.state === 'succeeded' && !item.result?.answer?.paragraphs?.length && <p className="muted">저장된 답변 본문이 없습니다.</p>}
      {item.failure && <p className="error">실패 사유 · {item.failure}</p>}
      {item.result && <details><summary>저장된 추가 답변 전체</summary><pre>{JSON.stringify(item.result, null, 2)}</pre></details>}
    </article>) : <p className="muted">추가 질문이 없습니다.</p>}</section>
    {!!data.events?.length && <details><summary>분석 단계 기록</summary><ol>{data.events.map((event, i) => <li key={i}>{new Date(event.at).toLocaleTimeString('ko-KR', { timeZone: 'Asia/Seoul' })} · {event.stage}</li>)}</ol></details>}
  </section>;
}
