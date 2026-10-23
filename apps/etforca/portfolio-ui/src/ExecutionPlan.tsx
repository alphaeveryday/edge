import type { Execution } from './api';
import { hasTradeAmounts, TradeAmount } from './TradeAmount';

const money = (value: string) => `${Number(value).toLocaleString('ko-KR')}원`;
export const executionReasons: Record<string, string> = {
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

export function ExecutionDetails({ plan }: { plan: Execution }) {
  const actionable = plan.status === 'ready' || plan.status === 'conditional';
  return <>
    {!actionable && <p className="notice">{plan.status === 'infeasible' ? '현재 조건으로는 이 목표를 실행할 수 없어요.' : '실행 전에 아래 정보를 확인해야 해요.'}</p>}
    {plan.reasons.map(reason => { const [code, account] = reason.split(':'); return <p className="notice" key={reason}>{account && `${account} · `}{code === 'CUSTOMER_CONSTRAINT_NOT_SATISFIED' && plan.status === 'infeasible' ? '기존 목표가 요청하신 조건에 맞지 않아 실행할 수 없어요.' : executionReasons[code] || '실행 조건을 더 확인해야 해요.'}</p>; })}
    {Number(plan.additional_krw) > 0 && <p className="funding-summary">{actionable ? '추가 현금' : '계산에 사용한 추가 현금'} <strong>{money(plan.additional_krw)}{actionable && ' 투입'}</strong>{plan.months != null && plan.monthly_krw != null && <span> · 매월 {money(plan.monthly_krw)}씩 {plan.months}개월</span>}</p>}
    {!actionable && plan.required_additional_krw != null && <p>{plan.required_additional_is_minimum === false ? '매도 없이 목표를 달성할 추가 자금의 상한' : '목표에 필요한 최소 추가 자금'}: <strong>{money(plan.required_additional_krw)}</strong></p>}
    {actionable && plan.months != null && plan.months > 1 && <p className="notice">{plan.months}개월 계획의 총 거래 금액이에요.</p>}
    {actionable && plan.accounts.filter(account => Number(account.deposit_krw) > 0 || Number(account.withdrawal_krw) > 0).map(account => <p key={account.account}>{account.account}{Number(account.deposit_krw) > 0 && ` · 납입 ${money(account.deposit_krw)}`}{Number(account.withdrawal_krw) > 0 && ` · 인출 ${money(account.withdrawal_krw)}`}</p>)}
    {!!plan.assumptions.length && <details><summary>계산에 적용한 조건</summary><ul className="report-bullets">{plan.assumptions.map(text => <li key={text}>{text}</li>)}</ul></details>}
  </>;
}

export function ExecutionPlan({ plan }: { plan: Execution }) {
  const actionable = plan.status === 'ready' || plan.status === 'conditional';
  return <section className="improvements execution-plan" data-analytics-section="changes">
    <h4>내 포트폴리오에서 바꿀 것</h4>
    <ExecutionDetails plan={plan} />
    {actionable && <ul className="improvement-list execution-actions">{plan.rows.map(row => <li key={`${row.instrument_id}:${row.account}`}><div>
      <div className="action-heading"><h5 title={row.name}>{row.instrument_id.startsWith('US:') ? row.instrument_id.slice(3) : row.name || row.instrument_id}</h5></div>
      {hasTradeAmounts(row) ? <TradeAmount row={row} /> : <p className="notice">거래 금액 확인 필요</p>}
      <small className="action-note">{row.account}</small>
    </div></li>)}</ul>}
  </section>;
}
