/**
 * PageSkeleton — 데이터 페이지 공통 로딩 자리 표시.
 * 로딩 중 빈 화면(return null)·empty-state 오표시("…없습니다" 깜빡임) 대신 이걸 보인다.
 * 폭이 다른 .skel 바 몇 줄을 카드에 담는 최소 구현 — 화면별 커스텀 스켈레톤은 두지 않는다.
 */
export interface PageSkeletonProps {
  /** 표시할 바 개수 (기본 4) */
  rows?: number;
}

const WIDTHS = ['42%', '100%', '87%', '64%', '93%', '71%'];

export function PageSkeleton({ rows = 4 }: PageSkeletonProps) {
  return (
    <div
      className="card card-pad"
      role="status"
      aria-label="불러오는 중"
      style={{ display: 'flex', flexDirection: 'column', gap: 12 }}
    >
      {Array.from({ length: rows }, (_, i) => (
        <span key={i} className="skel" style={{ width: WIDTHS[i % WIDTHS.length] }} />
      ))}
    </div>
  );
}
