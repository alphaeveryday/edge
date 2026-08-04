/* 목록·대시보드 테이블이 공유하는 셀 조각 + 조회 실패 표시 */
import { StatusBadge } from 'ui-kit';

/** 쿼리 실패를 빈 화면으로 위장하지 않는다 (Rule 12) */
export function LoadError() {
  return (
    <div className="card card-pad" style={{ fontSize: 12, color: 'var(--down)' }}>
      데이터를 불러오지 못했습니다. 잠시 후 새로고침해 주세요.
    </div>
  );
}
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

export function StatusCell({ it, showServing = false }: { it: Explanation; showServing?: boolean }) {
  return (
    <td>
      <span className="inline-flex items-center gap-1.5">
        {/* 서버가 UI 가 모르는 상태값을 보내도 빈 배지 대신 원문 코드를 보인다 */}
        <StatusBadge tone={STATUS_TONE[it.status]}>{STATUS_LABEL[it.status] ?? it.status}</StatusBadge>
        {/* 노출 head(ALPHA-744) — 상태(제공 자격)와 별개로 "지금 고객 화면의 그 판"을 가리킨다 */}
        {showServing && it.serving && (
          <StatusBadge tone="exposed" dot={false}>
            노출 중
          </StatusBadge>
        )}
      </span>
    </td>
  );
}

export function RiskCell({ it }: { it: Explanation }) {
  return (
    <td>
      {it.risk ? (
        <StatusBadge tone={RISK_TONE[it.risk]} dot={false}>
          {RISK_LABEL[it.risk]}
        </StatusBadge>
      ) : (
        <span style={{ color: 'var(--fg-4)' }}>—</span>
      )}
    </td>
  );
}
