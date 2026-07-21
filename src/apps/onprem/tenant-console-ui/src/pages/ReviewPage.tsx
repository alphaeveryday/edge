import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Delta, Icon, StatusBadge } from 'ui-kit';
import type { ReviewReason } from '../domains/explanations';
import { REASON_LABEL } from '../domains/explanations';
import { useExplanations } from '../domains/explanations/hooks';
import { RiskCell, StockCell } from './_shared/cells';

export function ReviewPage() {
  const navigate = useNavigate();
  const { data: items = [] } = useExplanations();

  const [q, setQ] = useState('');
  const [fReason, setFReason] = useState<ReviewReason | 'ALL'>('ALL');

  const keyword = q.trim().toLowerCase();
  const pending = items.filter((it) => it.status === 'REVIEW_REQUIRED');
  const filtered = pending.filter(
    (it) =>
      (!keyword || it.name.toLowerCase().includes(keyword) || it.code.toLowerCase().includes(keyword)) &&
      (fReason === 'ALL' || it.reviewReason === fReason),
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
          onChange={(e) => setFReason(e.target.value as ReviewReason | 'ALL')}
        >
          <option value="ALL">전체 사유</option>
          {(Object.keys(REASON_LABEL) as ReviewReason[]).map((r) => (
            <option key={r} value={r}>
              {REASON_LABEL[r]}
            </option>
          ))}
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
              <th>시장</th>
              <th className="col-num">등락률</th>
              <th>검수 사유</th>
              <th>위험 등급</th>
              <th className="col-muted">대기 시간</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((it) => (
              <tr key={it.id} className="cursor-pointer" onClick={() => navigate(`/review/${it.id}`)}>
                <StockCell name={it.name} code={it.code} />
                <td className="col-muted">{it.market}</td>
                <td className="col-num">
                  <Delta direction={it.direction} pct={it.changePct} />
                </td>
                <td>
                  <StatusBadge tone="warn">{it.reviewReason ? REASON_LABEL[it.reviewReason] : ''}</StatusBadge>
                </td>
                <RiskCell it={it} />
                <td className="col-muted num">{it.receivedRelative}</td>
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
