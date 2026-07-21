/* 목록·대시보드 테이블이 공유하는 셀 조각 */
import { StatusBadge } from 'ui-kit';
import type { Explanation } from '../../domains/explanations';
import { RISK_LABEL, RISK_TONE, STATUS_LABEL, STATUS_TONE } from '../../domains/explanations';

export function StockCell({ name, code }: { name: string; code: string }) {
  return (
    <td>
      <span className="font-semibold">{name}</span>{' '}
      <span className="num" style={{ color: 'var(--fg-4)', fontSize: 12 }}>
        {code}
      </span>
    </td>
  );
}

export function StatusCell({ it }: { it: Explanation }) {
  return (
    <td>
      <StatusBadge tone={STATUS_TONE[it.status]}>{STATUS_LABEL[it.status]}</StatusBadge>
    </td>
  );
}

export function RiskCell({ it }: { it: Explanation }) {
  return (
    <td>
      <StatusBadge tone={RISK_TONE[it.risk]} dot={false}>
        {RISK_LABEL[it.risk]}
      </StatusBadge>
    </td>
  );
}
