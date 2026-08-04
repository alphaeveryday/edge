import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Icon, PageSkeleton, StatusBadge } from 'ui-kit';
import type { ReviewReasonType } from '../domains/review';
import { AUTO_PUBLISH_CRITERIA, REASON_LABEL, reasonLabel } from '../domains/review';
import type { ConfidenceLevel } from '../domains/explanations';
import { CONFIDENCE_LABEL } from '../domains/explanations';
import { useReviewItems } from '../domains/review/hooks';
import { LoadError, StockCell } from './_shared/cells';

/**
 * Review Queue 목록(ALPHA-436) — status=REVIEW_REQUIRED 논리 작업함의 실계약 조회.
 * 등락률 컬럼은 실데이터 도착 전이라 두지 않는다(번들 확장 ALPHA-497) — 없는 값을
 * mock 으로 보여주지 않는다. 확신도는 원장 confidence_level 원값의 라벨 표시다(ALPHA-634).
 */
export function ReviewPage() {
  const navigate = useNavigate();
  const { data: items = [], isError, isPending } = useReviewItems();

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
        <select
          className="select"
          value={fReason}
          onChange={(e) => setFReason(e.target.value)}
        >
          <option value="ALL">전체 사유</option>
          {(Object.keys(REASON_LABEL) as ReviewReasonType[]).map((r) => (
            <option key={r} value={r}>
              {REASON_LABEL[r]}
            </option>
          ))}
          <option value={AUTO_PUBLISH_CRITERIA}>{reasonLabel(AUTO_PUBLISH_CRITERIA)}</option>
        </select>
        <div className="flex-1" />
        <span className="num" style={{ fontSize: 12, color: 'var(--fg-3)' }}>
          대기 {filtered.length}건
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
                  <div className="flex flex-wrap gap-1">
                    {it.reviewReasons.map((r) => (
                      <StatusBadge key={r} tone="warn">
                        {reasonLabel(r)}
                      </StatusBadge>
                    ))}
                  </div>
                </td>
                <td className="col-muted">{it.confidenceLevel ? (CONFIDENCE_LABEL[it.confidenceLevel as ConfidenceLevel] ?? it.confidenceLevel) : '—'}</td>
                <td className="col-muted num">
                  {it.receivedAt ? new Date(it.receivedAt).toLocaleString('sv-SE').slice(0, 16) : '—'}
                </td>
                <td className="text-right">
                  <button className="btn btn-sm">검수</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {filtered.length === 0 && (
          <div className="p-10 text-center" style={{ color: 'var(--fg-3)', fontSize: 12 }}>
            검수 대기 중인 설명이 없습니다.
          </div>
        )}
      </div>
    </div>
  );
}
