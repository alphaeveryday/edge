import type { Execution } from './api';

const money = (value: string) => `${Number(value).toLocaleString('ko-KR')}원`;
const reasons: Record<string, string> = {
  SAVED_TARGET_UNAVAILABLE: '기존 목표의 종목이나 계좌를 현재 조건으로 사용할 수 없어 대안을 다시 확인해야 해요.',
  CUSTOMER_CONSTRAINT_NOT_SATISFIED: '기존 목표가 현재 고객 필수 조건을 만족하는지 확인해야 해요.',
  CURRENT_AMOUNTS_REQUIRED: '보유 종목별 현재 평가금액이 필요해요.',
  INVESTMENT_AMOUNT_REQUIRED: '처음 투자할 금액이 필요해요.',
  ADDITIONAL_FUNDING_REQUIRED: '추가로 넣을 금액과 납입 주기를 확인해야 해요.',
  NO_SELL_ZERO_TARGET: '이미 보유한 종목의 목표를 0%로 만들려면 매도가 필요해요.',
  NO_SELL_VIOLATION: '매도 없이 이 목표 금액에 도달할 수 없어요.',
  ACCOUNT_OPENING_REQUIRED: '계좌가 개설되어 있는지 확인해야 해요.',
  ACCOUNT_WITHDRAWAL_REVIEW_REQUIRED: '다른 계좌로 옮기기 전에 인출 조건을 확인해야 해요.',
  ACCOUNT_CONTRIBUTION_LIMIT_REQUIRED: '금융사에서 남은 납입 한도를 확인해야 해요.',
  ACCOUNT_CONTRIBUTION_LIMIT_EXCEEDED: '입력한 남은 납입 한도를 초과해요.',
};

export function ExecutionPlan({ plan }: { plan: Execution }) {
  const actionable = plan.status === 'ready' || plan.status === 'conditional';
  return <section className="account-plans execution-plan" data-analytics-section="execution">
    <h4>금액으로 보는 리밸런싱</h4>
    <p className="notice">{plan.status === 'ready' ? '입력한 평가금액과 추가 자금으로 계산한 계획이에요.' : plan.status === 'conditional' ? '추가 납입으로 목표에 접근하는 조건부 계획이에요.' : plan.status === 'infeasible' ? '현재 조건으로는 이 목표를 실행할 수 없어요.' : '실행 전에 아래 정보를 확인해야 해요.'}</p>
    {plan.reasons.map(reason => { const [code, account] = reason.split(':'); return <p className="notice" key={reason}>{account && `${account} · `}{code === 'CUSTOMER_CONSTRAINT_NOT_SATISFIED' && plan.status === 'infeasible' ? '기존 목표가 요청하신 조건에 맞지 않아 실행할 수 없어요.' : reasons[code] || '실행 조건을 더 확인해야 해요.'}</p>; })}
    {plan.required_additional_krw != null && <p>목표에 필요한 최소 추가 자금: <strong>{money(plan.required_additional_krw)}</strong></p>}
    {plan.months != null && <p>매월 {money(plan.monthly_krw!)} 납입 시 <strong>{plan.months}개월</strong> · 총 추가 자금 {money(plan.additional_krw)}</p>}
    {actionable && <div className="execution-scroll" role="region" aria-label="리밸런싱 금액표" tabIndex={0}><table>
      <caption className="sr-only">현재 금액, 목표 금액과 필요한 거래</caption>
      <thead><tr><th>종목 · 계좌</th><th>현재</th><th>목표</th><th>매수</th><th>매도</th><th>현금 투입</th></tr></thead>
      <tbody>{plan.rows.map(row => <tr key={`${row.instrument_id}:${row.account}`}><th scope="row">{row.name || row.instrument_id}<small>{row.account}</small></th><td data-label="현재">{money(row.before_krw)}</td><td data-label="목표">{money(row.target_krw)}</td><td data-label="매수">{money(row.buy_krw)}</td><td data-label="매도">{money(row.sell_krw)}</td><td data-label="현금 투입">{money(row.cash_used_krw)}</td></tr>)}</tbody>
    </table></div>}
    {actionable && plan.accounts.map(account => <p key={account.account}>{account.account} · 납입 {money(account.deposit_krw)} · 인출 {money(account.withdrawal_krw)}</p>)}
    <ul className="report-bullets">{plan.assumptions.map(text => <li key={text}>{text}</li>)}</ul>
  </section>;
}
