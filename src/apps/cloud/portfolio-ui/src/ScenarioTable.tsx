export type Scenarios = { status: string; reason?: string; principal_krw?: string; monthly_krw?: string;
  assumptions?: string[]; rows: { annual_return_pct: string; years: number; future_krw: string; paid_krw: string }[] };

const money = (value: string) => `${BigInt(value).toLocaleString('ko-KR')}원`;
export function ScenarioTable({ scenarios }: { scenarios: Scenarios }) {
  return <section className="scenario-plan">
    <h4>수익률 가정별 금액 비교</h4>
    <p className="notice">미래 수익 예측이 아닌 조건부 계산이에요.</p>
    {scenarios.status !== 'available' ? <p>{scenarios.status === 'needs_input' ? '현재 평가금액과 확정된 납입 금액·주기를 확인해야 계산할 수 있어요.' : '계산 중 오류가 발생했어요. 다시 계산해 주세요.'}</p> : <>
      <p>시작 금액 {money(scenarios.principal_krw!)} · 월말 납입 {money(scenarios.monthly_krw!)}</p>
      <div style={{ overflowX: 'auto' }}><table><caption>아래 납입 조건과 일정한 연수익률을 적용한 계산</caption>
        <thead><tr><th>연수익률 가정</th><th>기간</th><th>총 납입</th><th>가정 결과</th></tr></thead>
        <tbody>{scenarios.rows.map(row => <tr key={`${row.annual_return_pct}:${row.years}`}><th scope="row">{row.annual_return_pct}%</th><td>{row.years}년</td><td>{money(row.paid_krw)}</td><td>{money(row.future_krw)}</td></tr>)}</tbody>
      </table></div>
    </>}
    <ul className="report-bullets">{scenarios.assumptions?.map(text => <li key={text}>{text}</li>)}</ul>
  </section>;
}
