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
import { CONFIDENCE_LABEL, CONFIDENCE_TONE, STATUS_LABEL, STATUS_TONE } from '../../domains/explanations';

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
      {/* 서버가 UI 가 모르는 상태값을 보내도 빈 배지 대신 원문 코드를 보인다 */}
      <StatusBadge tone={STATUS_TONE[it.status]}>{STATUS_LABEL[it.status] ?? it.status}</StatusBadge>
    </td>
  );
}

export function ConfidenceCell({ it }: { it: Explanation }) {
  return (
    <td>
      {it.confidence ? (
        <StatusBadge tone={CONFIDENCE_TONE[it.confidence]} dot={false}>
          {CONFIDENCE_LABEL[it.confidence]}
        </StatusBadge>
      ) : (
        <span style={{ color: 'var(--fg-4)' }}>—</span>
      )}
    </td>
  );
}
