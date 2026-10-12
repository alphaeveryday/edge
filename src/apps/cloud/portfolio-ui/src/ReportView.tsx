import { roles, titles, type Report } from './api';

export function ReportView({ report }: { report: Report }) {
  return <>
    <span className="eyebrow">ORCA PORTFOLIO</span><h1>내 투자,<br /><em>근거부터</em> 살펴봤어요.</h1>
    <p className="intro">분석 기준 {new Date(report.as_of).toLocaleString('ko-KR')}</p>
    <article className="card"><h2>내 포트폴리오</h2><p>입력한 평가금액 {Number(report.metrics.total_krw).toLocaleString('ko-KR')}원</p>
      {report.metrics.holdings.map((h, i) => <div className="allocation" key={i}><span>{h.name}<small>{h.account}</small></span><b>{h.weight_pct}%</b></div>)}
    </article>
    <article className="card evaluation"><h2>핵심 평가 정리</h2>
      {roles.map(role => <section key={role}><h3>{titles[role]}</h3>{report.sections[role].paragraphs.map((p, i) => <p key={i}>{p}</p>)}
        {report.sections[role].limitations.map((p, i) => <p className="notice" key={i}>{p}</p>)}
        {!!report.sections[role].evidence.length && <details><summary>판단에 사용한 근거</summary>{report.sections[role].evidence.map((e, i) => {
          const source = report.sources.find(s => s.id === e.source_id);
          return <blockquote key={i}>{e.quote}{source && <a href={source.url} target="_blank" rel="noreferrer">원문 확인 ↗</a>}</blockquote>;
        })}</details>}
      </section>)}
    </article>
    <article className="card"><h2>성과·수익률 데이터</h2><div className="metrics"><span>예상 1년 수익률<b>산출 전</b></span><span>1년 배당률<b>산출 전</b></span></div><h3>과거 성과</h3><p className="notice">{report.metrics.unavailable_reason}</p></article>
  </>;
}
