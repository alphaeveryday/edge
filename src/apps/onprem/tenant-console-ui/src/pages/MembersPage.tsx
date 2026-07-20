/* 구성원 페이지 — members 도메인 hook 만 의존한다 (mock 직접 import 없음). */
import { useState } from 'react';
import { Avatar, Badge, Icon, Modal, PageHeader, Panel, Segmented, toast } from '../components';
import { useMembers } from '../domains/members';
import type { MemberRole } from '../domains/members';

const ROLE_TONE: Record<MemberRole, 'info' | 'neutral' | 'warn'> = {
  Owner: 'info',
  Admin: 'info',
  Developer: 'neutral',
  Compliance: 'warn',
  Viewer: 'neutral',
};

export function MembersPage() {
  const { members, loading, error, invite } = useMembers();

  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<MemberRole>('Developer');
  const [submitting, setSubmitting] = useState(false);

  const submitInvite = async () => {
    const target = email.trim();
    if (!target) return;
    setSubmitting(true);
    try {
      await invite({ email: target, role });
      setOpen(false);
      setEmail('');
      toast(`${target} 으로 초대를 보냈습니다`);
    } catch {
      toast('초대 전송에 실패했습니다', 'bad');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div>
      <PageHeader
        title="구성원"
        badge={!loading && <Badge tone="neutral">{members.length}명</Badge>}
        desc="구성원을 초대·비활성화하고 소속 애플리케이션을 매핑합니다."
        actions={
          <button className="btn btn-pri" onClick={() => setOpen(true)}>
            <Icon n="plus" s={16} />
            구성원 초대
          </button>
        }
      />

      {error ? (
        <Panel>
          <div className="empty-state">
            <div className="empty-ico">
              <Icon n="alert" s={26} />
            </div>
            <div style={{ marginTop: 12, fontSize: 14, fontWeight: 600, color: 'var(--n-700)' }}>
              구성원을 불러오지 못했습니다
            </div>
            <div className="muted" style={{ fontSize: 12.5, marginTop: 4 }}>
              {error.message}
            </div>
          </div>
        </Panel>
      ) : (
        <Panel pad={false}>
          <table className="tbl hover">
            <thead>
              <tr>
                <th>구성원</th>
                <th>역할</th>
                <th>소속 앱</th>
                <th>상태</th>
                <th className="r">최근 활동</th>
              </tr>
            </thead>
            <tbody>
              {loading
                ? Array.from({ length: 5 }).map((_, i) => (
                    <tr key={i}>
                      <td colSpan={5}>
                        <div className="skel-bar" style={{ height: 18, width: '100%' }} />
                      </td>
                    </tr>
                  ))
                : members.map((m) => (
                    <tr key={m.id}>
                      <td>
                        <div className="row gap10">
                          <Avatar
                            initial={m.initial}
                            size={32}
                            tone={m.status === '활성' ? 'navy' : 'grey'}
                          />
                          <div>
                            <div className="t-strong" style={{ fontSize: 13.5 }}>
                              {m.name}
                            </div>
                            <div
                              className="mono"
                              style={{ fontSize: 11.5, color: 'var(--n-500)' }}
                            >
                              {m.email}
                            </div>
                          </div>
                        </div>
                      </td>
                      <td>
                        <Badge tone={ROLE_TONE[m.role]}>{m.role}</Badge>
                      </td>
                      <td style={{ fontSize: 13 }}>{m.apps}</td>
                      <td>
                        <Badge tone={m.status === '활성' ? 'ok' : 'warn'} dot>
                          {m.status}
                        </Badge>
                      </td>
                      <td className="r muted" style={{ fontSize: 12.5 }}>
                        {m.lastActive}
                      </td>
                    </tr>
                  ))}
            </tbody>
          </table>
        </Panel>
      )}

      {open && (
        <Modal
          title="구성원 초대"
          sub="회사 이메일로 초대 링크를 보냅니다"
          onClose={() => setOpen(false)}
          footer={
            <>
              <button className="btn btn-ghost" onClick={() => setOpen(false)}>
                취소
              </button>
              <button
                className="btn btn-pri"
                disabled={!email.trim() || submitting}
                onClick={submitInvite}
              >
                <Icon n="mail" s={15} />
                초대 보내기
              </button>
            </>
          }
        >
          <div className="col gap18">
            <div className="field">
              <label>이메일</label>
              <input
                className="input mono"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="name@hanbit.co.kr"
                autoFocus
              />
            </div>
            <div className="field">
              <label>역할</label>
              <Segmented
                opts={['Admin', 'Developer', 'Compliance', 'Viewer']}
                value={role}
                onChange={(v) => setRole(v as MemberRole)}
                block
              />
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}
