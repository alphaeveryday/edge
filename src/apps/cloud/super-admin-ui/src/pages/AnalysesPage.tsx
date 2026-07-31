import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Delta, Icon, PageSkeleton, StatusBadge } from 'ui-kit';
import type { AnalysisMarket, AnalysisStatus } from '../domains/analyses';
import { ANALYSIS_STATUS_LABEL, ANALYSIS_STATUS_TONE } from '../domains/analyses';
import { useAnalyses } from '../domains/analyses/hooks';
import { LoadError } from './_shared/LoadError';

export function AnalysesPage() {
  const navigate = useNavigate();
  const analysesQuery = useAnalyses();

  const [q, setQ] = useState('');
  const [fStatus, setFStatus] = useState<AnalysisStatus | 'ALL'>('ALL');
  const [fMarket, setFMarket] = useState<AnalysisMarket | 'ALL'>('ALL');

  if (analysesQuery.isError) return <LoadError />;
  if (analysesQuery.isPending) return <PageSkeleton rows={6} />;

  const keyword = q.trim().toLowerCase();
  const rows = analysesQuery.data
    .filter((a) => fStatus === 'ALL' || a.status === fStatus)
    .filter((a) => fMarket === 'ALL' || a.market === fMarket)
    .filter((a) => !keyword || `${a.name}${a.code}`.toLowerCase().includes(keyword));

  const segBtn = (value: AnalysisMarket | 'ALL', label: string) => (
    <button className={fMarket === value ? 'active' : ''} onClick={() => setFMarket(value)}>
      {label}
    </button>
  );

  return (
    <div className="flex max-w-[1200px] flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <label className="field field-search">
          <Icon name="search" className="ic" />
          <input placeholder="종목명 · 종목코드 검색" value={q} onChange={(e) => setQ(e.target.value)} />
        </label>
        <select
          className="select"
          value={fStatus}
          onChange={(e) => setFStatus(e.target.value as AnalysisStatus | 'ALL')}
        >
          <option value="ALL">전체 상태</option>
          {(Object.keys(ANALYSIS_STATUS_LABEL) as AnalysisStatus[]).map((s) => (
            <option key={s} value={s}>
              {ANALYSIS_STATUS_LABEL[s]}
            </option>
          ))}
        </select>
        <div className="seg">
          {segBtn('ALL', '전체 시장')}
          {segBtn('KRX', 'KRX')}
          {segBtn('NASDAQ', 'NASDAQ')}
        </div>
        <div className="flex-1" />
        <span className="t-xs" style={{ color: 'var(--fg-3)' }}>{rows.length}건</span>
      </div>

      <div className="card overflow-x-auto">
        <table className="table">
          <thead>
            <tr>
              <th>종목</th>
              <th>시장</th>
              <th className="col-num">등락률</th>
              <th>변동 기준 시각</th>
              <th>상태</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((a) => (
              <tr key={a.id} className="cursor-pointer" onClick={() => navigate(`/analyses/${a.id}`)}>
                <td>
                  <div className="flex flex-col gap-px">
                    <span className="font-semibold">{a.name}</span>
                    <span className="mono t-xs" style={{ color: 'var(--fg-3)' }}>{a.code}</span>
                  </div>
                </td>
                <td>
                  <span className="tag">{a.market}</span>
                </td>
                <td className="col-num">
                  <Delta direction={a.direction} pct={a.changePct} />
                </td>
                <td className="col-muted num whitespace-nowrap">{a.basisTime}</td>
                <td>
                  <StatusBadge tone={ANALYSIS_STATUS_TONE[a.status]}>{ANALYSIS_STATUS_LABEL[a.status]}</StatusBadge>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length === 0 && (
          <div className="p-10 text-center" style={{ color: 'var(--fg-3)', fontSize: 13 }}>
            조건에 맞는 분석 건이 없습니다.
          </div>
        )}
      </div>
    </div>
  );
}
