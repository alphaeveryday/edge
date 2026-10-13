import type { CSSProperties } from 'react';
import type { Evaluation, Report, Section } from './api';
import { chartColors, PerformanceChart, PortfolioDonut } from './ReportCharts';
import './report.css';

const axes = [['leadership', '시장 주도성'], ['profitability', '수익성'], ['stability', '안정성']] as const;
const grades = ['심각', '개선 필요', '보통', '훌륭', '매우 훌륭'];
const money = (value: string) => `${(Number(value) / 10000).toLocaleString('ko-KR', { maximumFractionDigits: 4 })}만 원`;
const gradeIndex = (value: number) => value >= 4.5 ? 4 : value >= 3.5 ? 3 : value >= 2.5 ? 2 : value >= 1.5 ? 1 : 0;

function Stars({ value }: { value: number }) {
  return <span className="stars" role="img" aria-label={`별 ${value}개`}>{Array.from({ length: 5 }, (_, i) => <i key={i} style={{ '--f': `${Math.max(0, Math.min(1, value - i)) * 100}%` } as CSSProperties}>★</i>)}</span>;
}
function Axes({ evaluation, recommended = false }: { evaluation?: Evaluation; recommended?: boolean }) {
  return <ul className={recommended ? "axes recommended-axes" : "axes"}>{axes.map(([key, label]) => <li key={key}><div><b>{label}</b>{evaluation?.scores ? <><Stars value={evaluation.scores[key]} /><em>{evaluation.scores[key]}</em></> : <span className="pending-score">평가 보류</span>}</div></li>)}</ul>;
}
function Bullets({ section }: { section: Section }) {
  return <><ul className="report-bullets">{section.paragraphs.map((p, i) => <li key={i}>{p}</li>)}</ul>{section.limitations.length > 0 && <p className="notice">{section.limitations.join(' ')}</p>}</>;
}
function Evidence({ section, report }: { section: Section; report: Report }) {
  if (!section.evidence.length) return null;
  return <details className="evaluation__sources"><summary>판단에 사용한 근거</summary>{section.evidence.map((e, i) => { const source = report.sources.find(s => s.id === e.source_id); return <blockquote key={i}>{e.quote}{source?.url && <a href={source.url} target="_blank" rel="noreferrer">원문 확인 ↗</a>}</blockquote>; })}</details>;
}

export function ReportView({ report }: { report: Report }) {
  const current = report.current_evaluation;
  const plan = report.recommendation;
  const colors = new Map<string, string>();
  plan?.allocations.forEach(h => { if (!colors.has(h.instrument_id)) colors.set(h.instrument_id, chartColors[colors.size % chartColors.length]); });
  const color = (id: string) => colors.get(id) || '#929daf';
  const composition = [...colors.keys()].map(id => {
    const rows = plan!.allocations.filter(h => h.instrument_id === id);
    return { ...rows[0], weight_pct: Number(rows.reduce((sum, h) => sum + h.weight_pct, 0).toFixed(2)) };
  });
  const free = report.insights ?? [{ title: '질문에 대한 답', content: report.sections.question }, { title: '포트폴리오 분석', content: report.sections.portfolio }];
  const parts = [{ title: '최종 투자 의견과 실행할 행동', content: report.sections.opinion }, { title: '앞으로의 전망', content: report.sections.outlook }, ...free];
  return <div id="report" className="step">
    <article className="ans lead"><div className="headrow"><small>ORCA AI의 최종 결론</small><details className="crit"><summary aria-label="판단 기준 보기">?</summary><div>{current?.rubric_version === 1 ? <><b>판단 기준</b><ul><li><b>등급</b>세 축 별점의 평균으로 정해요. 4.5 이상 매우 훌륭, 3.5 이상 훌륭, 2.5 이상 보통, 1.5 이상 개선 필요, 그 아래는 심각이에요.</li><li><b>시장 주도성</b>시가총액 상위 기업과 산업이 포트폴리오에서 차지하는 비중이에요.</li><li><b>수익성</b>담은 지수의 장기 수익률, 레버리지 여부, 보수율을 살펴봐요.</li><li><b>안정성</b>나라·통화·자산 종류의 수와 나쁜 해의 하락 폭을 살펴봐요.</li><li><b>자료</b>ETF 구성 종목과 과거 가격·보수율을 확인해 평가해요. 사용자의 선호는 점수에 넣지 않아요.</li></ul></> : <p>이전 평가 기준으로 작성된 분석이에요. 다시 분석하면 새 판단 기준이 적용돼요.</p>}</div></details></div>
      <p className="verdict"><em>{current?.overall != null ? grades[gradeIndex(current.overall)] : '내 구성 검토'}</em></p>
      {current?.overall != null && <><p className="total"><Stars value={current.overall} /><b>{current.overall}</b><span>/ 5</span></p><ol className="grade">{grades.map((g, i) => <li className={i === gradeIndex(current.overall!) ? 'on' : ''} key={g}>{g}</li>)}</ol></>}
      <Axes evaluation={current} /><div className="why"><i>이유</i><Bullets section={current?.reason || report.sections.portfolio} /><Evidence section={current?.reason || report.sections.portfolio} report={report} /></div>
      <small className="report-date">분석 기준 {new Date(report.as_of).toLocaleString('ko-KR')}</small>
    </article>
    <h3 className="sec">{plan ? 'ORCA AI의 추천 포트폴리오' : '분석한 포트폴리오'}</h3>
    {plan ? <article className="ans pf best">
      <div className="portfolio-hero"><div className="recommendation-head"><span className="recommendation-label">추천 포트폴리오</span><div className="recommended-rating" aria-label="추천 포트폴리오 종합 점수">{plan.evaluation.overall != null ? <div className="rating-line"><span className="rating-star" aria-hidden="true">★</span><b>{plan.evaluation.overall}</b><span className="rating-denominator">/ 5</span></div> : <small>평가 보류</small>}</div></div>
        <p className="todo">{plan.title}</p>
        <div className="mixviz"><PortfolioDonut holdings={composition.map(h => ({ name: h.name, weight_pct: h.weight_pct, color: color(h.instrument_id) }))} /><ul>{composition.map(h => <li key={h.instrument_id}><i style={{ background: color(h.instrument_id) }} /><span title={h.name}>{h.instrument_id.startsWith('US:') ? h.ticker : h.name}</span><b>{h.weight_pct}%</b></li>)}</ul></div>
        <Axes evaluation={plan.evaluation} recommended />
        <div className="forecast"><div><span>예상 1년 수익률</span><b>{plan.expected_return_1y == null ? '산출 전' : `${plan.expected_return_1y > 0 ? '+' : ''}${plan.expected_return_1y}%`}</b></div><div><span>1년 배당률</span><b>{plan.dividend_yield_1y == null ? '산출 전' : `${plan.dividend_yield_1y}%`}</b></div></div>
        {(plan.return_forecast || plan.dividend_basis) && <details className="forecast-basis"><summary>수익률·배당률 산정 근거</summary>{plan.return_forecast && <><p>{plan.return_forecast.reason}</p><p>{plan.return_forecast.method}</p></>}<p>{plan.dividend_basis}</p>{plan.target_return_1y != null && <p>연 {plan.target_return_1y}%는 목표 수익률이며 예상 수익률의 하한이 아니에요.</p>}{plan.dividend_observations?.map(row => <p key={row.instrument_id}>{row.instrument_id} · {row.trailing_yield_pct}% · {row.as_of}{row.source_url && <a href={row.source_url} target="_blank" rel="noreferrer"> 분배금 내역 ↗</a>}{row.method && <small>{row.method}</small>}{row.evidence.map((e, i) => <a key={i} href={e.url} target="_blank" rel="noreferrer"> 배당 출처 ↗</a>)}</p>)}</details>}
      </div>
      <section className="improvements"><div className="improvements-heading"><h4>내 포트폴리오에서 바꿀 것</h4></div>{plan.actions.length ? <ul className="improvement-list">{plan.actions.map((a, i) => <li key={i}><div><div className="action-heading"><i className="asset-dot" style={{ background: color(a.instrument_id) }} /><h5 title={a.name}>{a.instrument_id?.startsWith('US:') ? a.ticker : a.name} {a.action}</h5></div><p className="weight-change"><span>{a.before_pct == null ? '미입력' : `${a.before_pct}%`}</span><span>→</span><strong>{a.after_pct}%</strong></p>{new Set(plan.actions.map(action => action.account)).size > 1 && <small className="action-note">{a.account}</small>}</div></li>)}</ul> : <p className="notice">현재 구성을 유지하는 안이에요.</p>}</section>
      <section className="account-plans"><h4>계좌별 최종 투자 제안</h4>{[...new Set(plan.allocations.map(h => h.account))].map(account => { const rows = plan.allocations.filter(h => h.account === account); const hasAmount = rows.every(h => h.amount_krw !== null); return <article className="account-plan" key={account}><header><div><h5>{account}</h5></div><div className="account-total"><span>{hasAmount ? '최종 투자금' : '전체 대비 비중'}</span><strong>{hasAmount ? money(String(rows.reduce((sum, h) => sum + Number(h.amount_krw), 0))) : `${rows.reduce((sum, h) => sum + h.weight_pct, 0).toFixed(2).replace(/\.00$/, '')}%`}</strong></div></header><table><caption className="sr-only">{account} 종목별 최종 금액</caption><tbody>{rows.map(h => <tr key={h.instrument_id}><th scope="row" style={{ "--asset-color": color(h.instrument_id) } as CSSProperties}><b title={h.name}>{h.instrument_id.startsWith('US:') ? h.ticker : h.name}</b></th><td><strong>{h.amount_krw === null ? `${h.weight_pct}%` : money(h.amount_krw)}</strong></td></tr>)}</tbody></table></article>; })}{plan.total_krw === null && <p className="notice">투자 총액이 없어 추천 비중만 표시했어요.</p>}</section>
      <div className="why"><i>이렇게 고른 이유</i><Bullets section={plan.evaluation.reason} /><Evidence section={plan.evaluation.reason} report={report} /></div>
      <section className="performance"><h4>과거 성과</h4>{plan.historical_performance ? <PerformanceChart history={plan.historical_performance} /> : <p className="notice">{plan.unavailable_reason}</p>}</section>
    </article> : <article className="ans"><p className="notice">{report.optimization ? report.optimization.reason === 'RATING_BELOW_MINIMUM' ? '조사한 근거로 종합 4점 이상을 줄 수 있는 구성을 찾지 못해 추천을 보류했어요.' : report.optimization.status === 'no_match' ? '이번 비교에서 추천 기준을 충족한 구성을 찾지 못했어요.' : '기존 비중 또는 비교 자료가 충분하지 않아 추천을 보류했어요.' : '이전 형식의 분석입니다. 추천 구성·점수는 새 분석에서 확인할 수 있어요.'}</p>{report.metrics.total_krw !== null && <p className="fee"><span>입력한 평가금액</span><b>{money(report.metrics.total_krw)}</b></p>}<ul className="report-bullets">{report.metrics.holdings.map((h, i) => <li key={i}>{h.name} · {h.account} · {h.weight_pct == null ? '비중 미입력' : `${h.weight_pct}%`}</li>)}</ul>{!report.optimization && <section className="performance"><h4>과거 성과</h4><p className="notice">{report.metrics.unavailable_reason}</p></section>}</article>}
    <article className="evaluation"><h3>핵심 평가 정리</h3>{parts.map((part, i) => <section className={'evaluation__part' + (i === 0 ? ' action-first' : '')} key={i}><h4>{part.title}</h4><Bullets section={part.content} /><Evidence section={part.content} report={report} /></section>)}</article>
  </div>;
}
