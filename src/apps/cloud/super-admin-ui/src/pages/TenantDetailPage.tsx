import { useParams } from 'react-router-dom';
import { StatusBadge } from 'ui-kit';
import { ENV_TONE, TENANT_STATUS_LABEL, TENANT_STATUS_TONE } from '../domains/tenants';
import { useTenant } from '../domains/tenants/hooks';
import { LoadError } from './_shared/LoadError';

export function TenantDetailPage() {
  const { id } = useParams();
  const { tenant: t, isPending, isError } = useTenant(id);

  if (isError) return <LoadError />;
  if (isPending) return null;
  if (!t) {
    return (
      <div className="p-10 text-center" style={{ color: 'var(--fg-3)', fontSize: 13 }}>
        해당 테넌트를 찾을 수 없습니다.
      </div>
    );
  }

  const errRate = t.calls ? `${((t.errors / t.calls) * 100).toFixed(2)}%` : '0.00%';
  const barMax = Math.max(1, ...t.bars);
  const fmt = (n: number) => n.toLocaleString('ko-KR');

  return (
    <div className="flex max-w-[960px] flex-col gap-4">
      <div className="flex items-center gap-2.5">
        <span className="t-h1">{t.name}</span>
        <StatusBadge tone={ENV_TONE[t.env]} dot={false}>{t.env}</StatusBadge>
        <StatusBadge tone={TENANT_STATUS_TONE[t.status]}>{TENANT_STATUS_LABEL[t.status]}</StatusBadge>
      </div>

      <div className="grid items-start gap-4" style={{ gridTemplateColumns: '340px 1fr' }}>
        {/* 기본 정보 */}
        <div className="card">
          <div className="card-head">
            <span className="t-label">기본 정보</span>
          </div>
          <div className="flex flex-col gap-3.5 p-4">
            <div className="flex flex-col gap-0.5">
              <span className="t-label">테넌트명</span>
              <span className="t-body font-semibold">{t.name}</span>
              <span className="t-xs" style={{ color: 'var(--fg-3)' }}>{t.domain}</span>
            </div>
            <hr className="hr" />
            <div className="flex flex-col gap-0.5">
              <span className="t-label">환경 구분</span>
              <span className="t-body num">{t.env}</span>
            </div>
            <hr className="hr" />
            <div className="flex flex-col gap-0.5">
              <span className="t-label">생성일</span>
              <span className="t-body num">{t.created}</span>
            </div>
            <hr className="hr" />
            <div className="flex flex-col gap-0.5">
              <span className="t-label">테넌트 관리자</span>
              <span className="t-body">{t.admin}</span>
              <a className="t-xs" href={`mailto:${t.email}`}>{t.email}</a>
            </div>
          </div>
        </div>

        {/* 연결 상태 */}
        <div className="card">
          <div className="card-head">
            <span className="t-label">연결 상태</span>
          </div>
          <div className="flex flex-col gap-4 p-4">
            <div className="grid grid-cols-3 gap-3">
              <div className="flex flex-col gap-0.5">
                <span className="t-label">마지막 동기화 시각</span>
                <span className="t-body num font-semibold">{t.lastSyncAbs}</span>
                <span className="t-xs" style={{ color: 'var(--fg-3)' }}>{t.lastSync}</span>
              </div>
              <div className="flex flex-col gap-0.5">
                <span className="t-label">최근 24시간 호출</span>
                <span className="t-body num font-semibold">{fmt(t.calls)}건</span>
              </div>
              <div className="flex flex-col gap-0.5">
                <span className="t-label">오류</span>
                <span className="t-body num font-semibold">
                  {fmt(t.errors)}건 <span style={{ fontWeight: 400, color: 'var(--fg-3)' }}>({errRate})</span>
                </span>
              </div>
            </div>
            <div className="flex flex-col gap-1.5">
              <span className="t-label">최근 24시간 호출 현황</span>
              <div className="flex items-end gap-[3px] pt-2" style={{ height: 88 }}>
                {t.bars.map((v, i) => (
                  <div
                    key={i}
                    title={`${24 - i}시간 전 · 호출 ${fmt(v * 23)}건`}
                    className="flex-1 rounded-t-[2px]"
                    style={{
                      height: `${Math.max(3, Math.round((v / barMax) * 100))}%`,
                      background: t.status === 'SYNC_DELAYED' && i > 19 ? 'var(--down)' : 'var(--gray-300)',
                    }}
                  />
                ))}
              </div>
              <div className="flex justify-between">
                <span className="t-xs num" style={{ color: 'var(--fg-4)' }}>-24h</span>
                <span className="t-xs num" style={{ color: 'var(--fg-4)' }}>-12h</span>
                <span className="t-xs num" style={{ color: 'var(--fg-4)' }}>현재</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
