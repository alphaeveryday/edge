/* 애플리케이션 페이지 — applications 도메인 hook 만 의존한다 (mock 직접 import 없음). */
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Badge,
  EnvBadge,
  Icon,
  Modal,
  PageHeader,
  Panel,
  Segmented,
  toast,
} from '../components';
import { useApplications } from '../domains/applications';

export function ApplicationsPage() {
  const nav = useNavigate();
  const { apps, loading, error, createApp } = useApplications();

  const [open, setOpen] = useState(false);
  const [name, setName] = useState('');
  const [type, setType] = useState<'모바일' | '웹'>('모바일');
  const [env, setEnv] = useState('production');
  const [submitting, setSubmitting] = useState(false);

  const submitCreate = async () => {
    const target = name.trim();
    if (!target) return;
    setSubmitting(true);
    try {
      const created = await createApp({ name: target, type, env });
      setOpen(false);
      setName('');
      toast(`${created.name} 앱이 생성되었습니다`);
      nav(`/applications/${created.slug}/overview`);
    } catch {
      toast('앱 생성에 실패했습니다', 'bad');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div>
      <PageHeader
        title="애플리케이션"
        badge={!loading && <Badge tone="neutral">{apps.length}개</Badge>}
        desc="등록된 앱의 트래픽·키·웹훅을 관리하고 위젯을 임베드합니다."
        actions={
          <button className="btn btn-pri" onClick={() => setOpen(true)}>
            <Icon n="plus" s={16} />
            앱 추가
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
              애플리케이션을 불러오지 못했습니다
            </div>
            <div className="muted" style={{ fontSize: 12.5, marginTop: 4 }}>
              {error.message}
            </div>
          </div>
        </Panel>
      ) : (
        <Panel pad={false}>
          {loading
            ? Array.from({ length: 3 }).map((_, i) => (
                <div key={i} className="lrow">
                  <div className="skel-bar" style={{ height: 36, width: '100%' }} />
                </div>
              ))
            : apps.map((a) => (
                <div
                  key={a.id}
                  className="lrow"
                  style={{ cursor: 'pointer' }}
                  onClick={() => nav(`/applications/${a.slug}/overview`)}
                >
                  <div className="app-ico">
                    <Icon n={a.type === '모바일' ? 'grid' : 'building2'} s={18} />
                  </div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="t-strong" style={{ fontSize: 13.5 }}>
                      {a.name}
                    </div>
                    <div className="num" style={{ fontSize: 11.5, color: 'var(--n-500)' }}>
                      {a.calls} 호출 · 에러 {a.callsErr} · MAU {a.mau}
                    </div>
                  </div>
                  <div className="row gap8" style={{ flex: 'none' }}>
                    <span className="num" style={{ fontSize: 12, color: 'var(--n-500)' }}>
                      키 {a.keys} · 훅 {a.hooks}
                    </span>
                    <EnvBadge env={a.env} />
                    <Icon n="chevR" s={16} style={{ color: 'var(--n-300)' }} />
                  </div>
                </div>
              ))}
        </Panel>
      )}

      {open && (
        <Modal
          title="앱 추가"
          sub="새 애플리케이션을 등록합니다"
          onClose={() => setOpen(false)}
          footer={
            <>
              <button className="btn btn-ghost" onClick={() => setOpen(false)}>
                취소
              </button>
              <button
                className="btn btn-pri"
                disabled={!name.trim() || submitting}
                onClick={submitCreate}
              >
                <Icon n="plus" s={15} />
                생성
              </button>
            </>
          }
        >
          <div className="col gap18">
            <div className="field">
              <label>앱 이름</label>
              <input
                className="input"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="한빛 MTS"
                autoFocus
              />
            </div>
            <div className="field">
              <label>유형</label>
              <Segmented
                opts={['모바일', '웹']}
                value={type}
                onChange={(v) => setType(v as '모바일' | '웹')}
                block
              />
            </div>
            <div className="field">
              <label>환경</label>
              <Segmented
                opts={[
                  { v: 'production', l: 'production' },
                  { v: 'staging', l: 'staging' },
                ]}
                value={env}
                onChange={setEnv}
                block
              />
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}
