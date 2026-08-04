import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Delta, Icon, PageSkeleton, StatusBadge } from 'ui-kit';
import type { Analysis, AnalysisMarket, AnalysisStatus } from '../domains/analyses';
import { ANALYSIS_STATUS_LABEL, ANALYSIS_STATUS_TONE } from '../domains/analyses';
import { useAnalyses } from '../domains/analyses/hooks';
import { MOCK_ANALYSES } from '../mock/preview';
import { EmptyRealNotice, MockChip, MockPreview } from './_shared/MockPreview';
import { LoadError } from './_shared/LoadError';

/** 목록 본체 — 실데이터든 검수용 목데이터든 같은 화면을 그린다(렌더 경로를 복제하지 않는다) */
function AnalysesBody({ items, mock = false }: { items: Analysis[]; mock?: boolean }) {
  const navigate = useNavigate();
  const [q, setQ] = useState('');
  const [fStatus, setFStatus] = useState<AnalysisStatus | 'ALL'>('ALL');
  const [fMarket, setFMarket] = useState<AnalysisMarket | 'ALL'>('ALL');

  const keyword = q.trim().toLowerCase();
  const rows = items
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
        {mock && <MockChip />}
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
              <tr
                key={a.id}
                className={mock ? undefined : 'cursor-pointer'}
                /* 목데이터 행은 상세로 내려가지 않는다 — 없는 분석을 여는 죽은 링크가 된다 */
                onClick={mock ? undefined : () => navigate(`/analyses/${a.id}`)}
              >
                <td>
                  <div className="flex flex-col gap-px">
                    <span className="font-semibold">
                      {a.name} {mock && <MockChip />}
                    </span>
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

export function AnalysesPage() {
  const analysesQuery = useAnalyses();

  if (analysesQuery.isError) return <LoadError error={analysesQuery.error} />;
  if (analysesQuery.isPending) return <PageSkeleton rows={6} />;

  /* 실데이터가 0건이면 목록·필터의 의미를 볼 수 없다 — 사실을 먼저 밝히고 검수용 목을 따로 붙인다 */
  if (analysesQuery.data.length === 0) {
    return (
      <div className="flex flex-col gap-4">
        <EmptyRealNotice>
          원장(explanation_result)에 기록된 가격 변동 분석이 아직 없습니다.
        </EmptyRealNotice>
        <MockPreview>
          <AnalysesBody items={MOCK_ANALYSES} mock />
        </MockPreview>
      </div>
    );
  }

  return <AnalysesBody items={analysesQuery.data} />;
}
