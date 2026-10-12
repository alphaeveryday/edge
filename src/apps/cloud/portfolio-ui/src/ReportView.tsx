import { roles, titles, type Report } from './api';

const colors = ['#5b7cfa', '#9fd0ff', '#f2b866', '#aebdfd', '#7089fb'];
export function ReportView({ report }: { report: Report }) {
  let offset = 0;
  return <div id="report" className="step">
    <article className="ans lead"><div className="headrow"><small>ORCA AI의 최종 결론</small></div><p className="verdict"><em>내 구성 검토</em></p><ul className="axes">{['시장 주도성', '수익성', '안정성'].map(name => <li key={name}><div><b>{name}</b><span className="pending-score">산출 전</span></div></li>)}</ul><p className="why"><i>이유</i>{report.sections.opinion.paragraphs[0]}</p><small className="report-date">분석 기준 {new Date(report.as_of).toLocaleString('ko-KR')}</small></article>
    <h3 className="sec">ORCA AI의 투자 의견</h3>
    <article className="ans pf best"><div className="recommendation-head"><small>검토 결과</small><div className="forecast"><div><span>예상 1년 수익률</span><b>산출 전</b></div><div><span>1년 배당률</span><b>산출 전</b></div></div></div><p className="todo">{report.metrics.holdings.length ? '현재 포트폴리오' : '계좌별 투자 계획'}</p>
      {!!report.metrics.holdings.length && <div className="mixviz"><div className="donut md" role="img" aria-label="입력한 ETF 비중"><svg viewBox="0 0 42 42">{report.metrics.holdings.map((h, i) => { const weight = h.weight_pct ?? 100 / report.metrics.holdings.length; const start = offset; offset += weight; return <circle key={i} cx="21" cy="21" r="15.915" fill="none" stroke={colors[i % colors.length]} strokeWidth="6.4" strokeDasharray={`${Math.max(0, weight - .8)} ${100 - Math.max(0, weight - .8)}`} strokeDashoffset={-start} />; })}</svg><div className="donut__c"><b>{report.metrics.holdings.length}개</b></div></div><ul>{report.metrics.holdings.map((h, i) => <li key={i}><i style={{ background: colors[i % colors.length] }} /><span>{h.name}<em>{h.account}</em></span><b>{h.weight_pct === null ? '미입력' : `${h.weight_pct}%`}</b></li>)}</ul></div>}
      {report.metrics.total_krw && <p className="fee"><span>입력한 평가금액</span><b>{Number(report.metrics.total_krw).toLocaleString('ko-KR')}원</b></p>}
      {report.metrics.holdings.some(h => h.weight_pct === null) && <p className="hint sm">비중을 적지 않아 그림은 같은 크기로 표시했어요.</p>}
      <section className="performance"><h4>과거 성과</h4><p className="hint sm">{report.metrics.unavailable_reason}</p></section>
    </article>
    <article className="ans evaluation"><h3>핵심 평가 정리</h3>{roles.map(role => <section className="evaluation__part" key={role}><h4>{titles[role]}</h4>{report.sections[role].paragraphs.map((p, i) => <p key={i}>{p}</p>)}{report.sections[role].limitations.map((p, i) => <p className="dialog-note" key={i}>{p}</p>)}{!!report.sections[role].evidence.length && <details className="evaluation__sources"><summary>판단에 사용한 근거</summary>{report.sections[role].evidence.map((e, i) => { const source = report.sources.find(s => s.id === e.source_id); return <blockquote key={i}>{e.quote}{source?.url && <a href={source.url} target="_blank" rel="noreferrer">원문 확인 ↗</a>}</blockquote>; })}</details>}</section>)}</article>
  </div>;
}
