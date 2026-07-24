import { StatusBadge } from 'ui-kit';
import type { SourceStatus } from '../domains/sources';
import { useSourceReport } from '../domains/sources/hooks';
import { LoadError } from './_shared/LoadError';

const SOURCE_STATUS_LABEL: Record<SourceStatus, string> = {
  COLLECTING: '정상 수집',
  DELAYED: '수집 지연',
};

export function SourcesPage() {
  const { data: report, isPending, isError } = useSourceReport();

  if (isError) return <LoadError />;
  if (isPending) return null;

  return (
    <div className="flex max-w-[1100px] flex-col gap-4">
      <div className="card">
        <div className="card-head">
          <span className="t-label">데이터 소스 수집 상태</span>
          <span className="t-xs num" style={{ color: 'var(--fg-3)' }}>마지막 점검 {report.checkedAt}</span>
        </div>
        <table className="table">
          <thead>
            <tr>
              <th>소스</th>
              <th>공급자</th>
              <th>상태</th>
              <th>마지막 수집</th>
              <th className="col-num">최근 24시간</th>
            </tr>
          </thead>
          <tbody>
            {report.sources.map((s) => (
              <tr key={s.name}>
                <td className="font-semibold">{s.name}</td>
                <td className="col-muted">{s.provider}</td>
                <td>
                  <StatusBadge tone={s.status === 'COLLECTING' ? 'active' : 'warn'}>
                    {SOURCE_STATUS_LABEL[s.status]}
                  </StatusBadge>
                </td>
                <td className="num">{s.lastCollected}</td>
                <td className="col-num num">{s.volume}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>
        수집 지연 상태의 소스는 재시도 큐에 자동 등록되며, 30분 이상 지연 시 운영팀에 알림이 발송됩니다.
      </p>
    </div>
  );
}
