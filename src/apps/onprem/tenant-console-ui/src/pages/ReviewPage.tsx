import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Icon, PageSkeleton, Select, StatusBadge } from 'ui-kit';
import type { ReviewReasonType } from '../domains/review';
import { AUTO_PUBLISH_CRITERIA, REASON_LABEL, gateReasonLabel, reasonLabel } from '../domains/review';
import type { ConfidenceLevel } from '../domains/explanations';
import { useReviewItems } from '../domains/review/hooks';
import { useInfiniteScroll } from '../lib/pagination';
import { ConfidenceCell, LoadError, StockCell } from './_shared/cells';
import { relativeFromNow } from '../lib/time';

/**
 * Review Queue 목록(ALPHA-436) — status=REVIEW_REQUIRED 논리 작업함의 실계약 조회.
 * 등락률 컬럼은 실데이터 도착 전이라 두지 않는다(번들 확장 ALPHA-497) — 없는 값을
 * mock 으로 보여주지 않는다. 확신도는 원장 confidence_level 원값의 라벨 표시다(ALPHA-634).
 */
export function ReviewPage() {
  const navigate = useNavigate();
  const {
    data: items = [],
    isError,
    isPending,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = useReviewItems();
  const sentinelRef = useInfiniteScroll(fetchNextPage, hasNextPage && !isFetchingNextPage);

  const [q, setQ] = useState('');
  const [fReason, setFReason] = useState<string>('ALL');

  if (isError) return <LoadError />;
  // 로딩 중 빈 목록이 "…없습니다" empty-state 로 오표시되지 않게 게이트한다.
  if (isPending) return <PageSkeleton rows={6} />;

  const keyword = q.trim().toLowerCase();
  const filtered = items.filter(
    (it) =>
      (!keyword ||
        (it.name ?? '').toLowerCase().includes(keyword) ||
        (it.ticker ?? '').toLowerCase().includes(keyword)) &&
      (fReason === 'ALL' || it.reviewReasons.includes(fReason)),
  );

  return (
    <div className="flex max-w-[1100px] flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <label className="field field-search">
          <Icon name="search" className="ic" />
          <input
            placeholder="종목명 또는 종목코드 검색"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </label>
        <Select
          aria-label="검수 사유 필터"
          value={fReason}
          onChange={setFReason}
          options={[
            { value: 'ALL', label: '전체 사유' },
            ...(Object.keys(REASON_LABEL) as ReviewReasonType[]).map((r) => ({ value: r, label: REASON_LABEL[r] })),
            { value: AUTO_PUBLISH_CRITERIA, label: reasonLabel(AUTO_PUBLISH_CRITERIA) },
          ]}
        />
        <div className="flex-1" />
        <span className="num" style={{ fontSize: 12, color: 'var(--fg-3)' }}>
          대기 {filtered.length}
          {hasNextPage ? '+' : ''}건
        </span>
      </div>

      <div className="card">
        <table className="table">
          <thead>
            <tr>
              <th>종목</th>
              <th>변동 요인 요약</th>
              <th>검수 사유</th>
              <th className="col-muted">확신도</th>
              <th className="col-muted">수신</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((it) => (
              <tr key={it.id} className="cursor-pointer" onClick={() => navigate(`/review/${it.id}`)}>
                <StockCell name={it.name ?? '(종목 미상)'} code={it.ticker ?? '—'} />
                <td style={{ maxWidth: 360 }}>
                  <div className="truncate" title={it.summary}>
                    {it.summary}
                  </div>
                </td>
                <td>
                  {/* 룰 무관 판정은 서버가 "자동 제공 기준 미충족" 하나로 뭉치는데, 상세는
                      판정 당시 기준으로 구체 문구를 만든다 — 목록도 같은 말을 써야 한 항목의
                      사유가 화면마다 달라지지 않는다(ALPHA-774). 기준을 못 만들면 일반 사유가
                      남아 "왜 걸렸는지"가 사라지지 않는다. */}
                  <div className="flex flex-wrap gap-1">
                    {it.reviewReasons
                      .filter(
                        (r) =>
                          r !== AUTO_PUBLISH_CRITERIA ||
                          !it.gateChecks.some((g) => gateReasonLabel(g)),
                      )
                      .map((r) => (
                        <StatusBadge key={r} tone="warn">
                          {reasonLabel(r)}
                        </StatusBadge>
                      ))}
                    {it.gateChecks
                      .map((g) => gateReasonLabel(g))
                      .filter((label): label is string => label !== null)
                      .map((label) => (
                        <StatusBadge key={label} tone="warn">
                          {label}
                        </StatusBadge>
                      ))}
                  </div>
                </td>
                {/* 설명 목록과 같은 배지 — 같은 값을 화면마다 다른 모양으로 그리지 않는다. */}
                <ConfidenceCell level={it.confidenceLevel as ConfidenceLevel | null} />
                <td className="col-muted t-data">
                  {relativeFromNow(it.receivedAt)}
                </td>
                <td className="text-right">
                  <button className="btn btn-sm">검수</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {filtered.length === 0 && !hasNextPage && (
          <div className="p-10 text-center" style={{ color: 'var(--fg-3)', fontSize: 12 }}>
            검수 대기 중인 설명이 없습니다.
          </div>
        )}
      </div>
      {/* 무한 스크롤 센티널(ALPHA-914) — 보이면 다음 50건을 로드한다. */}
      <div ref={sentinelRef} />
      {isFetchingNextPage && (
        <div className="pb-4 text-center" style={{ color: 'var(--fg-3)', fontSize: 12 }}>
          불러오는 중…
        </div>
      )}
    </div>
  );
}
