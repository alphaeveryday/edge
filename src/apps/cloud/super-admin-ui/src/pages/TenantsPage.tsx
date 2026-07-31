import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Icon, Modal, StatusBadge, toast } from 'ui-kit';
import type { TenantCreateEnv, TenantStatus } from '../domains/tenants';
import { ENV_TONE, TENANT_STATUS_LABEL, TENANT_STATUS_TONE } from '../domains/tenants';
import { apiMessage } from '../api/client';
import { useCreateTenant, useTenants } from '../domains/tenants/hooks';
import { LoadError } from './_shared/LoadError';

const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

export function TenantsPage() {
  const navigate = useNavigate();
  const tenantsQuery = useTenants();
  const create = useCreateTenant();

  const [q, setQ] = useState('');
  const [fStatus, setFStatus] = useState<TenantStatus | 'ALL'>('ALL');

  const [createOpen, setCreateOpen] = useState(false);
  const [fName, setFName] = useState('');
  const [fEnv, setFEnv] = useState<TenantCreateEnv>('PoC');
  const [fAdmin, setFAdmin] = useState('');
  const [fEmail, setFEmail] = useState('');
  const [fMemo, setFMemo] = useState('');
  const [fError, setFError] = useState('');

  if (tenantsQuery.isError) return <LoadError />;
  if (tenantsQuery.isPending) return null;

  const keyword = q.trim().toLowerCase();
  const rows = tenantsQuery.data
    .filter((t) => fStatus === 'ALL' || t.status === fStatus)
    .filter((t) => !keyword || `${t.name}${t.domain}${t.admin}${t.email}`.toLowerCase().includes(keyword));

  const openCreate = () => {
    setCreateOpen(true);
    setFName('');
    setFEnv('PoC');
    setFAdmin('');
    setFEmail('');
    setFMemo('');
    setFError('');
  };

  const submitCreate = () => {
    if (!fName.trim()) return setFError('테넌트명을 입력해 주세요.');
    if (!fAdmin.trim()) return setFError('관리자 이름을 입력해 주세요.');
    if (!EMAIL_RE.test(fEmail.trim())) return setFError('올바른 관리자 이메일을 입력해 주세요.');
    create.mutate(
      { name: fName.trim(), env: fEnv, admin: fAdmin.trim(), email: fEmail.trim(), memo: fMemo },
      {
        onSuccess: () => {
          setCreateOpen(false);
          setQ('');
          setFStatus('ALL');
          toast('테넌트가 생성되었습니다.');
        },
        // 서버 실패 사유를 모달 안에 인라인으로 보인다(훅이 전역 토스트를 끔).
        onError: (err) => setFError(apiMessage(err, '테넌트를 생성하지 못했습니다.')),
      },
    );
  };

  const segBtn = (value: TenantStatus | 'ALL', label: string) => (
    <button className={fStatus === value ? 'active' : ''} onClick={() => setFStatus(value)}>
      {label}
    </button>
  );

  return (
    <div className="flex max-w-[1200px] flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <label className="field field-search">
          <Icon name="search" className="ic" />
          <input
            placeholder="테넌트명 · 관리자 · 도메인 검색"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </label>
        <div className="seg">
          {segBtn('ALL', '전체 상태')}
          {segBtn('ACTIVE', '정상')}
          {segBtn('SYNC_DELAYED', '동기화 지연')}
          {segBtn('ONBOARDING', '미연결(온보딩 중)')}
        </div>
        <div className="flex-1" />
        <span className="t-xs" style={{ color: 'var(--fg-3)' }}>{rows.length}개 테넌트</span>
        <button className="btn btn-primary" onClick={openCreate}>
          <Icon name="plus" className="ic" />
          테넌트 생성
        </button>
      </div>

      <div className="card overflow-x-auto">
        <table className="table">
          <thead>
            <tr>
              <th>테넌트</th>
              <th>환경</th>
              <th>상태</th>
              <th>관리자</th>
              <th>생성일</th>
              <th>마지막 동기화</th>
              <th className="col-num">24H 호출</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((t) => (
              <tr key={t.id} className="cursor-pointer" onClick={() => navigate(`/tenants/${t.id}`)}>
                <td>
                  <div className="flex flex-col gap-px">
                    <span className="font-semibold">{t.name}</span>
                    <span className="t-xs" style={{ color: 'var(--fg-3)' }}>{t.domain}</span>
                  </div>
                </td>
                <td>
                  <StatusBadge tone={ENV_TONE[t.env]} dot={false}>{t.env}</StatusBadge>
                </td>
                <td>
                  <StatusBadge tone={TENANT_STATUS_TONE[t.status]}>{TENANT_STATUS_LABEL[t.status]}</StatusBadge>
                </td>
                <td>
                  <div className="flex flex-col gap-px">
                    <span>{t.admin}</span>
                    <span className="t-xs" style={{ color: 'var(--fg-3)' }}>{t.email}</span>
                  </div>
                </td>
                <td className="col-muted num whitespace-nowrap">{t.created}</td>
                <td className="col-muted whitespace-nowrap">{t.lastSync}</td>
                <td className="col-num num">{t.calls.toLocaleString('ko-KR')}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length === 0 && (
          <div className="p-10 text-center" style={{ color: 'var(--fg-3)', fontSize: 13 }}>
            조건에 맞는 테넌트가 없습니다.
          </div>
        )}
      </div>

      {/* ============ 테넌트 생성 모달 ============ */}
      <Modal
        open={createOpen}
        title="테넌트 생성"
        width={440}
        onClose={() => setCreateOpen(false)}
        footer={
          <>
            <button className="btn" onClick={() => setCreateOpen(false)}>
              취소
            </button>
            <button className="btn btn-primary" onClick={submitCreate}>
              생성
            </button>
          </>
        }
      >
        <div className="flex flex-col gap-3.5 p-5">
          <div className="flex flex-col gap-1">
            <span className="t-label">
              테넌트명 <span style={{ color: 'var(--down)' }}>*</span>
            </span>
            <label className="field w-full box-border">
              <input placeholder="예: 미래에셋증권" value={fName} onChange={(e) => setFName(e.target.value)} />
            </label>
          </div>
          <div className="flex flex-col gap-1">
            <span className="t-label">환경 구분</span>
            {/* IA 어휘(PoC/Production) — 레거시 Dev 는 생성 선택지가 아니다. */}
            <div className="seg self-start">
              <button className={fEnv === 'PoC' ? 'active' : ''} onClick={() => setFEnv('PoC')}>
                PoC
              </button>
              <button
                className={fEnv === 'Production' ? 'active' : ''}
                onClick={() => setFEnv('Production')}
              >
                Production
              </button>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="flex flex-col gap-1">
              <span className="t-label">
                관리자 이름 <span style={{ color: 'var(--down)' }}>*</span>
              </span>
              <label className="field w-full box-border">
                <input placeholder="홍길동" value={fAdmin} onChange={(e) => setFAdmin(e.target.value)} />
              </label>
            </div>
            <div className="flex flex-col gap-1">
              <span className="t-label">
                관리자 이메일 <span style={{ color: 'var(--down)' }}>*</span>
              </span>
              <label className="field w-full box-border">
                <input placeholder="admin@firm.co.kr" value={fEmail} onChange={(e) => setFEmail(e.target.value)} />
              </label>
            </div>
          </div>
          <div className="flex flex-col gap-1">
            <span className="t-label">메모</span>
            <textarea
              className="textarea"
              rows={3}
              placeholder="계약 조건, 담당 조직 등"
              style={{ fontSize: 12, lineHeight: 1.5 }}
              value={fMemo}
              onChange={(e) => setFMemo(e.target.value)}
            />
          </div>
          {fError && <span className="t-xs" style={{ color: 'var(--down)' }}>{fError}</span>}
        </div>
      </Modal>
    </div>
  );
}
