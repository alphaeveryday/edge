/* 검수용 목데이터 미리보기 틀 (ALPHA-738).
 *
 * 실데이터가 0건이면 화면의 목적을 판단할 수 없다. 그래서 **실제 상태를 먼저 밝히고**, 그
 * 아래에 테두리로 완전히 분리된 목데이터 영역을 둔다. 순서를 뒤집거나 경계를 흐리면 그 순간
 * "0건인 화면"과 "목데이터가 찬 화면"이 구분되지 않는다 — 실측 0을 목값으로 위장하는 셈이다.
 *
 * 목데이터는 렌더링 전용이다. API 응답·원장은 건드리지 않는다.
 */
import type { ReactNode } from 'react';
import '../../styles/mock-preview.css';

/** 실데이터가 왜 비었는지 먼저 말한다 — 기존 empty state 문구를 그대로 넣는다 */
export function EmptyRealNotice({ children }: { children: ReactNode }) {
  return (
    <div className="card card-pad">
      <p className="t-sm m-0" style={{ fontWeight: 600 }}>
        실데이터 0건
      </p>
      <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 4 }}>
        {children}
      </p>
    </div>
  );
}

/** 목데이터 영역 — 배지·문구·테두리로 실데이터 영역과 갈라 둔다 */
export function MockPreview({ children }: { children: ReactNode }) {
  return (
    <section className="mock-preview" aria-label="화면 검수용 목데이터 미리보기">
      <div className="mock-preview-head">
        <span className="chip mock-chip">MOCK</span>
        <span className="t-xs">
          아래 내용은 화면 검수를 위한 목데이터이며 실제 운영 데이터가 아닙니다.
        </span>
      </div>
      <div className="mock-preview-body">{children}</div>
    </section>
  );
}

/** 카드·행 단위 표시 — 목값이 실제 값처럼 보이지 않게 */
export function MockChip({ label = 'MOCK' }: { label?: string }) {
  return <span className="chip mock-chip">{label}</span>;
}
