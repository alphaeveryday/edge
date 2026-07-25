import { useState } from 'react';
import { Icon, Modal, StatusBadge, toast } from 'ui-kit';
import { useSession } from '../domains/session/hooks';
import type { Member, MemberRole } from '../domains/users';
import { ROLE_LABEL } from '../domains/users';
import { useDeactivateMember, useMembers, useRegisterMember } from '../domains/users/hooks';
import { LoadError } from './_shared/cells';

export function UsersPage() {
  const { data: session } = useSession();
  const isAdmin = session?.role === 'TENANT_ADMIN';
  // 비관리자는 조회 자체를 보내지 않는다(403 대신 아래 권한 안내). 세션 로딩 중에도 대기.
  const { data: members = [], isError } = useMembers(isAdmin);
  const register = useRegisterMember();
  const deactivate = useDeactivateMember();

  const [registerOpen, setRegisterOpen] = useState(false);
  const [email, setEmail] = useState('');
  const [name, setName] = useState('');
  const [role, setRole] = useState<MemberRole>('COMPLIANCE_REVIEWER');
  const [password, setPassword] = useState('');
  const [deactivateTarget, setDeactivateTarget] = useState<Member | null>(null);

  const confirmDeactivate = () => {
    if (!deactivateTarget) return;
    deactivate.mutate(deactivateTarget.id, {
      onSuccess: () => {
        setDeactivateTarget(null);
        toast('사용자를 비활성화했습니다.');
      },
      onError: (err) => {
        // 마지막 관리자(409) 등 서버 사유를 그대로 보인다.
        const msg = (err as { body?: { message?: string } })?.body?.message;
        toast(msg ?? '비활성화하지 못했습니다.');
      },
    });
  };

  const submit = () => {
    const emailValue = email.trim();
    const nameValue = name.trim();
    if (!emailValue || !emailValue.includes('@')) {
      toast('올바른 이메일을 입력하세요.');
      return;
    }
    if (!nameValue) {
      toast('이름을 입력하세요.');
      return;
    }
    // 공백 유무만 trim 으로 판정하고 자격증명은 원문 그대로 보낸다 — 서버 등록·로그인이
    // 모두 원문을 쓰므로, UI 가 앞뒤 공백을 지우면 사용자가 로그인하지 못한다.
    const passwordValue = password.trim() ? password : undefined;
    register.mutate(
      { email: emailValue, name: nameValue, role, password: passwordValue },
      {
        onSuccess: () => {
          setRegisterOpen(false);
          toast('사용자를 등록했습니다.');
        },
        onError: (err) => {
          // 중복 이메일(409)·유효성(400) 등 서버 사유를 그대로 보인다.
          const msg = (err as { body?: { message?: string } })?.body?.message;
          toast(msg ?? '사용자를 등록하지 못했습니다.');
        },
      },
    );
  };

  // 사용자 관리는 TENANT_ADMIN 전용(permission-matrix.md) — API 도 403 이라, 비관리자에겐
  // 조회를 시도해 LoadError 를 띄우는 대신 권한 안내를 보인다(직접 URL 접근 방어).
  if (session && session.role !== 'TENANT_ADMIN') {
    return (
      <div className="card card-pad" style={{ fontSize: 12, color: 'var(--fg-3)' }}>
        사용자 및 권한 관리는 관리자(Tenant Admin) 전용입니다.
      </div>
    );
  }

  if (isError) return <LoadError />;

  return (
    <div className="flex max-w-[960px] flex-col gap-4">
      <div className="flex items-center gap-2">
        <span className="num" style={{ fontSize: 12, color: 'var(--fg-3)' }}>
          {members.length}명
        </span>
        <div className="flex-1" />
        <button
          className="btn btn-primary"
          onClick={() => {
            setRegisterOpen(true);
            setEmail('');
            setName('');
            setRole('COMPLIANCE_REVIEWER');
            setPassword('');
          }}
        >
          <Icon name="plus" className="ic" strokeWidth={1.8} />
          사용자 등록
        </button>
      </div>

      <div className="card">
        <table className="table">
          <thead>
            <tr>
              <th>이름</th>
              <th>이메일</th>
              <th>역할</th>
              <th>상태</th>
              <th className="col-muted">최근 로그인</th>
              <th className="col-muted" style={{ textAlign: 'right' }}>관리</th>
            </tr>
          </thead>
          <tbody>
            {members.map((m) => (
              <tr key={m.id}>
                <td>
                  <div className="flex items-center gap-2">
                    <div
                      className="flex flex-none items-center justify-center rounded-full"
                      style={{ width: 24, height: 24, background: 'var(--gray-200)', color: 'var(--fg-2)', fontSize: 10, fontWeight: 600 }}
                    >
                      {m.name[0]}
                    </div>
                    <span className="font-semibold">{m.name}</span>
                  </div>
                </td>
                <td className="col-muted">{m.email}</td>
                <td>
                  <span className="chip">{ROLE_LABEL[m.role]}</span>
                </td>
                <td>
                  <StatusBadge tone={m.status === 'ACTIVE' ? 'active' : 'warn'}>
                    {m.status === 'ACTIVE' ? '활성' : '비활성'}
                  </StatusBadge>
                </td>
                <td className="col-muted num">{m.lastLogin ?? '—'}</td>
                <td style={{ textAlign: 'right' }}>
                  {m.status === 'ACTIVE' ? (
                    <button className="btn btn-sm" onClick={() => setDeactivateTarget(m)}>
                      비활성화
                    </button>
                  ) : (
                    <span style={{ color: 'var(--fg-4)' }}>—</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div style={{ fontSize: 11, color: 'var(--fg-4)', lineHeight: 1.6 }}>
        역할 안내 — 관리자: 조직·정책·사용자 관리 · 검수자: 검수 및 정책 관리 · 운영자: 운영 · 읽기 전용: 조회
      </div>

      <Modal open={registerOpen} title="사용자 등록" onClose={() => setRegisterOpen(false)}>
        <div className="flex flex-col gap-3 p-4">
          <div className="flex flex-col gap-1">
            <span style={{ fontSize: 11, color: 'var(--fg-3)' }}>이메일</span>
            <label className="field">
              <input placeholder="name@example.com" value={email} onChange={(e) => setEmail(e.target.value)} />
            </label>
          </div>
          <div className="flex flex-col gap-1">
            <span style={{ fontSize: 11, color: 'var(--fg-3)' }}>이름</span>
            <label className="field">
              <input placeholder="홍길동" value={name} onChange={(e) => setName(e.target.value)} />
            </label>
          </div>
          <div className="flex flex-col gap-1">
            <span style={{ fontSize: 11, color: 'var(--fg-3)' }}>역할</span>
            <select className="select w-full" value={role} onChange={(e) => setRole(e.target.value as MemberRole)}>
              <option value="TENANT_ADMIN">관리자</option>
              <option value="COMPLIANCE_REVIEWER">검수자</option>
              <option value="OPERATOR">운영자</option>
              <option value="READ_ONLY">읽기 전용</option>
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <span style={{ fontSize: 11, color: 'var(--fg-3)' }}>초기 비밀번호 (선택)</span>
            <label className="field">
              <input
                type="password"
                placeholder="데모 로컬 로그인용"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </label>
          </div>
          <div style={{ fontSize: 11, color: 'var(--fg-4)', lineHeight: 1.6 }}>
            관리자가 직접 등록합니다(초대 메일 없음). 비밀번호를 비워두면 조직 SSO 전용 계정이 되어
            데모 로컬 로그인은 불가합니다.
          </div>
          <div className="mt-1 flex justify-end gap-2">
            <button className="btn" onClick={() => setRegisterOpen(false)}>
              취소
            </button>
            <button className="btn btn-primary" onClick={submit}>
              등록
            </button>
          </div>
        </div>
      </Modal>

      <Modal
        open={deactivateTarget !== null}
        title="사용자 비활성화"
        onClose={() => setDeactivateTarget(null)}
      >
        <div className="flex flex-col gap-3 p-4">
          <div style={{ fontSize: 13, lineHeight: 1.6 }}>
            <span className="font-semibold">{deactivateTarget?.name}</span>(
            {deactivateTarget?.email}) 계정을 비활성화하시겠습니까? 비활성화하면 로그인할 수 없습니다.
          </div>
          <div className="mt-1 flex justify-end gap-2">
            <button className="btn" onClick={() => setDeactivateTarget(null)}>
              취소
            </button>
            <button className="btn btn-danger" onClick={confirmDeactivate}>
              비활성화
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
