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
import type { ConfidenceLevel, Explanation } from '../../domains/explanations';
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

export function StatusCell({ it, showServing = false }: { it: Explanation; showServing?: boolean }) {
  return (
    <td>
      <span className="inline-flex items-center gap-1.5">
        {/* 서버가 UI 가 모르는 상태값을 보내도 빈 배지 대신 원문 코드를 보인다 */}
        <StatusBadge tone={STATUS_TONE[it.status]}>{STATUS_LABEL[it.status] ?? it.status}</StatusBadge>
        {/* 노출 head(ALPHA-744) — 상태(제공 자격)와 별개로 "지금 고객 화면의 그 판"을 가리킨다 */}
        {showServing && it.serving && (
          <StatusBadge tone="exposed" dot={false}>
            제공 중
          </StatusBadge>
        )}
      </span>
    </td>
  );
}

/** 확신도 셀 — 설명 목록과 검수 목록이 공유한다(같은 값이 화면마다 다른 모양이면 안 된다).
 * 도메인 형이 아니라 값을 받는 이유가 그것이다: review 는 confidenceLevel, explanations 는
 * confidence 로 필드명이 다르다. */
export function ConfidenceCell({ level }: { level?: string | null }) {
  if (!level) {
    return (
      <td>
        <span style={{ color: 'var(--fg-4)' }}>—</span>
      </td>
    );
  }
  // 와이어 값은 문자열이라 어휘 밖 등급이 올 수 있다(서버 선배포 등). 라벨이 없으면
  // 배지가 빈 칸으로 그려지므로 원값을 그대로 낸다 — 확신도 정보가 사라지면 안 된다.
  // hasOwn 으로 가른다 — 어휘 밖 값이 'constructor' 류면 프로토타입 프로퍼티(함수)가
  // 잡혀 폴백을 건너뛰고 렌더에서 터진다.
  if (!Object.hasOwn(CONFIDENCE_LABEL, level)) return <td className="col-muted">{level}</td>;
  const label = CONFIDENCE_LABEL[level as ConfidenceLevel];
  return (
    <td>
      <StatusBadge tone={CONFIDENCE_TONE[level as ConfidenceLevel]} dot={false}>
        {label}
      </StatusBadge>
    </td>
  );
}
