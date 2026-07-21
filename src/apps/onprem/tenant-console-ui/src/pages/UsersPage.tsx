import { useState } from 'react';
import { Icon, Modal, StatusBadge, toast } from 'ui-kit';
import { useSession } from '../domains/session/hooks';
import type { MemberRole } from '../domains/users';
import { useInviteMember, useMembers } from '../domains/users/hooks';

export function UsersPage() {
  const { data: members = [] } = useMembers();
  const { data: session } = useSession();
  const invite = useInviteMember();

  const [inviteOpen, setInviteOpen] = useState(false);
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<MemberRole>('Compliance');

  const sendInvite = () => {
    const value = email.trim();
    if (!value || !value.includes('@')) {
      toast('올바른 이메일을 입력하세요.');
      return;
    }
    invite.mutate(
      { email: value, role },
      {
        onSuccess: () => {
          setInviteOpen(false);
          toast('초대 메일을 발송했습니다.');
        },
      },
    );
  };

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
            setInviteOpen(true);
            setEmail('');
            setRole('Compliance');
          }}
        >
          <Icon name="plus" className="ic" strokeWidth={1.8} />
          사용자 초대
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
                  <span className="chip">{m.role}</span>
                </td>
                <td>
                  <StatusBadge tone={m.status === 'ACTIVE' ? 'active' : 'warn'}>
                    {m.status === 'ACTIVE' ? '활성' : '초대 대기'}
                  </StatusBadge>
                </td>
                <td className="col-muted num">{m.lastLogin}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div style={{ fontSize: 11, color: 'var(--fg-4)', lineHeight: 1.6 }}>
        역할 안내 — Admin: 조직·정책·사용자 관리 · Compliance: 검수 및 정책 관리
      </div>

      <Modal open={inviteOpen} title="사용자 초대" onClose={() => setInviteOpen(false)}>
        <div className="flex flex-col gap-3 p-4">
          <div className="flex flex-col gap-1">
            <span style={{ fontSize: 11, color: 'var(--fg-3)' }}>이메일</span>
            <label className="field">
              <input
                placeholder={`name@${session?.tenantDomain ?? ''}`}
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </label>
          </div>
          <div className="flex flex-col gap-1">
            <span style={{ fontSize: 11, color: 'var(--fg-3)' }}>역할</span>
            <select className="select w-full" value={role} onChange={(e) => setRole(e.target.value as MemberRole)}>
              <option value="Admin">Admin</option>
              <option value="Compliance">Compliance</option>
            </select>
          </div>
          <div style={{ fontSize: 11, color: 'var(--fg-4)', lineHeight: 1.6 }}>
            초대 메일이 발송되며, 조직 이메일 도메인({session?.tenantDomain})만 가입할 수 있습니다.
          </div>
          <div className="mt-1 flex justify-end gap-2">
            <button className="btn" onClick={() => setInviteOpen(false)}>
              취소
            </button>
            <button className="btn btn-primary" onClick={sendInvite}>
              초대 보내기
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
