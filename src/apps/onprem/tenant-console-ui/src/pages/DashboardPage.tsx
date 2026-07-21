import { useNavigate } from 'react-router-dom';
import { Delta } from 'ui-kit';
import { FEED_DOT_COLOR, FEED_LABEL } from '../domains/explanations';
import { useExplanations, useFeedStatus } from '../domains/explanations/hooks';
import { LoadError, RiskCell, StatusCell, StockCell } from './_shared/cells';

export function DashboardPage() {
  const navigate = useNavigate();
  const explanationsQuery = useExplanations();
  const feedQuery = useFeedStatus();

  if (explanationsQuery.isError || feedQuery.isError) return <LoadError />;
  // 로딩 중 0건·빈 반입 정보를 실데이터처럼 보이지 않게 — 둘 다 로드된 뒤 렌더
  if (explanationsQuery.isPending || feedQuery.isPending) return null;

  const items = explanationsQuery.data;
  const feed = feedQuery.data;
  const count = (status: string) => items.filter((it) => it.status === status).length;

  return (
    <div className="flex max-w-[1200px] flex-col gap-6">
      <div>
        <div className="t-label mb-3">주요 현황</div>
        <div className="grid grid-cols-6 gap-3">
          <div className="kpi">
            <div className="kpi-label">가격 변동 설명 반입 상태</div>
            <div className="kpi-value flex items-center gap-2" style={{ fontSize: 18 }}>
              {feed && (
                <>
                  <span className="dot" style={{ width: 9, height: 9, background: FEED_DOT_COLOR[feed.state] }} />
                  {FEED_LABEL[feed.state]}
                </>
              )}
            </div>
            <div className="kpi-sub">
              최근 반입 {feed?.lastReceivedRelative} · 오늘 {feed?.todayReceived}건
            </div>
          </div>
          <div className="kpi">
            <div className="kpi-label">자동 제공</div>
            <div className="kpi-value">{count('AUTO_PUBLISHED')}</div>
            <div className="kpi-sub">전체 {items.length}건 중</div>
          </div>
          <div className="kpi cursor-pointer" onClick={() => navigate('/review')}>
            <div className="kpi-label">검수 대기</div>
            <div className="kpi-value" style={{ color: 'var(--warn)' }}>
              {count('REVIEW_REQUIRED')}
            </div>
            <div className="kpi-sub">
              <a>검수 대기 목록 →</a>
            </div>
          </div>
          <div className="kpi">
            <div className="kpi-label">점검 차단</div>
            <div className="kpi-value" style={{ color: 'var(--down)' }}>
              {count('BLOCKED')}
            </div>
            <div className="kpi-sub">점검 기준 위반</div>
          </div>
          <div className="kpi">
            <div className="kpi-label">검수 반려</div>
            <div className="kpi-value">{count('REJECTED')}</div>
            <div className="kpi-sub">검수자 반려 처리</div>
          </div>
          <div className="kpi">
            <div className="kpi-label">제공 중단</div>
            <div className="kpi-value">{count('UNPUBLISHED')}</div>
            <div className="kpi-sub">운영자 수동 중단</div>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <span className="t-label">최근 가격 변동 설명 요약</span>
          <button className="btn btn-sm btn-ghost" onClick={() => navigate('/explanations')}>
            전체 보기
          </button>
        </div>
        <table className="table">
          <thead>
            <tr>
              <th>종목</th>
              <th>시장</th>
              <th className="col-num">등락률</th>
              <th>제공 상태</th>
              <th>위험 등급</th>
              <th className="col-muted">반입</th>
            </tr>
          </thead>
          <tbody>
            {[...items]
              .sort((a, b) => b.receivedAt.localeCompare(a.receivedAt))
              .slice(0, 6)
              .map((it) => (
                <tr key={it.id} className="cursor-pointer" onClick={() => navigate(`/explanations/${it.id}`)}>
                  <StockCell name={it.name} code={it.code} />
                  <td className="col-muted">{it.market}</td>
                  <td className="col-num">
                    <Delta direction={it.direction} pct={it.changePct} />
                  </td>
                  <StatusCell it={it} />
                  <RiskCell it={it} />
                  <td className="col-muted num">{it.receivedRelative}</td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
