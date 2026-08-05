import { useState } from 'react';
import { Icon, Modal, PageSkeleton, Select, StatusBadge, toast } from 'ui-kit';
import { apiMessage } from '../api/client';
import { useSession } from '../domains/session/hooks';
import type { Member, MemberRole } from '../domains/users';
import { ROLE_LABEL } from '../domains/users';
import { useChangeRole, useDeactivateMember, useMembers, useRegisterMember } from '../domains/users/hooks';
import { LoadError } from './_shared/cells';

// 역할 셀렉트 옵션 — 표시 순서 고정(권한 높은 순), 라벨은 ROLE_LABEL SSOT.
const ROLE_OPTIONS: MemberRole[] = ['TENANT_ADMIN', 'COMPLIANCE_REVIEWER', 'OPERATOR', 'READ_ONLY'];
const roleOptions = ROLE_OPTIONS.map((r) => ({ value: r, label: ROLE_LABEL[r] }));

export function UsersPage() {
  const { data: session } = useSession();
  const isAdmin = session?.role === 'TENANT_ADMIN';
  // 비관리자는 조회 자체를 보내지 않는다(403 대신 아래 권한 안내). 세션 로딩 중에도 대기.
  const { data: members = [], isError, isPending } = useMembers(isAdmin);
  const register = useRegisterMember();
  const deactivate = useDeactivateMember();
  const changeRole = useChangeRole();

  const [registerOpen, setRegisterOpen] = useState(false);
  const [email, setEmail] = useState('');
  const [name, setName] = useState('');
  const [role, setRole] = useState<MemberRole>('COMPLIANCE_REVIEWER');
  const [password, setPassword] = useState('');
  const [deactivateTarget, setDeactivateTarget] = useState<Member | null>(null);
  const [roleTarget, setRoleTarget] = useState<Member | null>(null);
  const [newRole, setNewRole] = useState<MemberRole>('READ_ONLY');

  const confirmDeactivate = () => {
    if (!deactivateTarget) return;
    deactivate.mutate(deactivateTarget.id, {
      onSuccess: () => {
        setDeactivateTarget(null);
        toast('사용자를 비활성화했습니다.');
      },
      // 서버 사유(마지막 관리자 409 등)가 없을 때도 맥락 폴백을 유지한다 — 전역(일반 문구)보다 늦게 떠 덮는다.
      onError: (err) => toast(apiMessage(err, '비활성화하지 못했습니다.')),
    });
  };

  const confirmChangeRole = () => {
    if (!roleTarget || newRole === roleTarget.role) return;
    const target = roleTarget;
    changeRole.mutate(
      { id: target.id, role: newRole },
      {
        onSuccess: () => {
          // 늦게 도착한 콜백이 다른 대상의 모달을 닫지 않게, 요청 대상이 그대로일 때만 닫는다.
          setRoleTarget((current) => (current?.id === target.id ? null : current));
          toast('역할을 변경했습니다.');
        },
        onError: (err) => {
          // 서버 사유(마지막 관리자 강등·경쟁 변경 409 등) 우선, 없으면 맥락 폴백.
          toast(apiMessage(err, '역할을 변경하지 못했습니다.'));
          // 실패 시에도 모달을 닫는다 — stale 현재 역할을 근거로 한 재시도가 방금 이루어진
          // 다른 관리자의 변경을 확인 없이 덮어쓰지 않게, 갱신된 목록에서 다시 열게 한다.
          setRoleTarget((current) => (current?.id === target.id ? null : current));
        },
      },
    );
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
        // 서버 사유(중복 이메일 409·유효성 400) 우선, 없으면 맥락 폴백.
        onError: (err) => toast(apiMessage(err, '사용자를 등록하지 못했습니다.')),
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
  // 세션·목록 로딩 중 빈 목록 오표시 방지 — 비관리자는 위 권한 안내가 먼저 잡는다.
  if (isPending) return <PageSkeleton />;

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
                <td className="col-muted t-data">{m.lastLogin ?? '—'}</td>
                <td style={{ textAlign: 'right' }}>
                  <div className="flex items-center justify-end gap-1">
                    {/* 자기 자신 역할 변경은 서버가 403(직무 분리, permission-matrix.md) — 버튼도 숨겨 이중 방어. */}
                    {m.email !== session?.email && (
                      <button
                        className="btn btn-sm"
                        onClick={() => {
                          setRoleTarget(m);
                          setNewRole(m.role);
                        }}
                      >
                        역할 변경
                      </button>
                    )}
                    {m.status === 'ACTIVE' ? (
                      <button className="btn btn-sm" onClick={() => setDeactivateTarget(m)}>
                        비활성화
                      </button>
                    ) : (
                      m.email === session?.email && <span style={{ color: 'var(--fg-4)' }}>—</span>
                    )}
                  </div>
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
            <Select
              aria-label="역할"
              block
              value={role}
              onChange={(v) => setRole(v as MemberRole)}
              options={roleOptions}
            />
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
        open={roleTarget !== null}
        title="역할 변경"
        // pending 중 닫기·재개봉을 막는다 — 늦은 성공 콜백이 재개봉된 모달을 닫는 경쟁 차단.
        onClose={() => {
          if (!changeRole.isPending) setRoleTarget(null);
        }}
      >
        <div className="flex flex-col gap-3 p-4">
          <div style={{ fontSize: 13, lineHeight: 1.6 }}>
            <span className="font-semibold">{roleTarget?.name}</span>({roleTarget?.email})의 역할을
            변경합니다. 현재 역할: {roleTarget ? ROLE_LABEL[roleTarget.role] : ''}
          </div>
          <div className="flex flex-col gap-1">
            <span style={{ fontSize: 11, color: 'var(--fg-3)' }}>새 역할</span>
            <Select
              aria-label="새 역할"
              block
              value={newRole}
              onChange={(v) => setNewRole(v as MemberRole)}
              options={roleOptions}
            />
          </div>
          <div style={{ fontSize: 11, color: 'var(--fg-4)', lineHeight: 1.6 }}>
            역할 변경은 전건 감사 로그에 기록되며, 대상 사용자에게는 다음 요청부터 즉시 적용됩니다.
          </div>
          <div className="mt-1 flex justify-end gap-2">
            <button
              className="btn"
              onClick={() => setRoleTarget(null)}
              disabled={changeRole.isPending}
            >
              취소
            </button>
            <button
              className="btn btn-primary"
              onClick={confirmChangeRole}
              disabled={!roleTarget || newRole === roleTarget.role || changeRole.isPending}
            >
              변경
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
