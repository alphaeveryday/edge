import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Icon, PageSkeleton } from 'ui-kit';
import type { ConfidenceLevel, ServeStatus } from '../domains/explanations';
import { CONFIDENCE_LABEL, STATUS_LABEL } from '../domains/explanations';
import { useExplanations } from '../domains/explanations/hooks';
import { ConfidenceCell, LoadError, StatusCell, StockCell } from './_shared/cells';

export function ExplanationsPage() {
  const navigate = useNavigate();
  const { data: items = [], isError, isPending } = useExplanations();

  const [q, setQ] = useState('');
  const [fStatus, setFStatus] = useState<ServeStatus | 'ALL'>('ALL');
  const [fConfidence, setFConfidence] = useState<ConfidenceLevel | 'ALL'>('ALL');

  if (isError) return <LoadError />;
  // 로딩 중 빈 목록이 "…없습니다" empty-state 로 오표시되지 않게 게이트한다.
  if (isPending) return <PageSkeleton rows={6} />;

  const keyword = q.trim().toLowerCase();
  const filtered = items.filter(
    (it) =>
      (!keyword || it.name.toLowerCase().includes(keyword) || it.code.toLowerCase().includes(keyword)) &&
      (fStatus === 'ALL' || it.status === fStatus) &&
      (fConfidence === 'ALL' || it.confidence === fConfidence),
  );

  return (
    <div className="flex max-w-[1200px] flex-col gap-4">
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
          value={fStatus}
          onChange={(e) => setFStatus(e.target.value as ServeStatus | 'ALL')}
        >
          <option value="ALL">전체 상태</option>
          {(Object.keys(STATUS_LABEL) as ServeStatus[]).map((s) => (
            <option key={s} value={s}>
              {STATUS_LABEL[s]}
            </option>
          ))}
        </select>
        <select
          className="select"
          value={fConfidence}
          onChange={(e) => setFConfidence(e.target.value as ConfidenceLevel | 'ALL')}
        >
          <option value="ALL">전체 확신도</option>
          {(Object.keys(CONFIDENCE_LABEL) as ConfidenceLevel[]).map((c) => (
            <option key={c} value={c}>
              {CONFIDENCE_LABEL[c]}
            </option>
          ))}
        </select>
        <div className="flex-1" />
        <span className="num" style={{ fontSize: 12, color: 'var(--fg-3)' }}>
          {filtered.length}건
        </span>
      </div>

      <div className="card">
        <table className="table">
          <thead>
            <tr>
              <th>종목</th>
              <th>제공 상태</th>
              <th>확신도</th>
              <th className="col-muted">기준시각</th>
              <th className="col-muted">반입</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((it) => (
              <tr key={it.id} className="cursor-pointer" onClick={() => navigate(`/explanations/${it.id}`)}>
                <StockCell name={it.name} code={it.code} />
                <StatusCell it={it} showServing />
                <ConfidenceCell it={it} />
                <td className="col-muted num">{it.explanationAsOf}</td>
                <td className="col-muted num">{it.receivedRelative}</td>
                <td className="text-right" style={{ color: 'var(--fg-4)' }}>
                  <Icon name="chevronRight" size={14} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {filtered.length === 0 && (
          <div className="p-10 text-center" style={{ color: 'var(--fg-3)', fontSize: 12 }}>
            조건에 해당하는 가격 변동 설명이 없습니다.
          </div>
        )}
      </div>
    </div>
  );
}
