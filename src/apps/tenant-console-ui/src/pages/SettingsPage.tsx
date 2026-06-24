/* 설정 페이지 — settings 도메인 hook 만 의존한다 (mock 직접 import 없음). */
import { useState } from 'react';
import { Badge, Icon, PageHeader, Panel, Toggle, copyText, toast } from '../components';
import { useSettings } from '../domains/settings';
import type { NotificationSettings } from '../domains/settings';

/** 알림 토글 행 정의 — key 를 keyof 로 좁혀 strict 모드를 만족시킨다. */
const NOTI_ROWS: { key: keyof NotificationSettings; label: string }[] = [
  { key: 'ev', label: '이벤트 분석 알림' },
  { key: 'quota', label: '이상 트래픽 알림' },
  { key: 'comp', label: '컴플라이언스 경고' },
  { key: 'weekly', label: '주간 리포트 메일' },
];

const TABS: [string, string][] = [
  ['조직', 'building2'],
  ['정책', 'shieldCheck'],
  ['알림', 'bell'],
];

export function SettingsPage() {
  const { org, presets, notifications, loading, error, setNotification } = useSettings();
  const [sec, setSec] = useState('조직');

  return (
    <div>
      <PageHeader
        title="설정"
        desc="조직 정보, 정책 프리셋, 알림을 한 곳에서 관리합니다."
        actions={
          <button className="btn btn-pri" onClick={() => toast('설정이 저장되었습니다')}>
            <Icon n="check" s={16} />
            저장
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
              설정을 불러오지 못했습니다
            </div>
            <div className="muted" style={{ fontSize: 12.5, marginTop: 4 }}>
              {error.message}
            </div>
          </div>
        </Panel>
      ) : loading || !org || !notifications ? (
        <Panel>
          <div className="col gap16">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="skel-bar" style={{ height: 18, width: '100%' }} />
            ))}
          </div>
        </Panel>
      ) : (
        <>
          <div className="tabs">
            {TABS.map((t) => (
              <button
                key={t[0]}
                className={'tab' + (sec === t[0] ? ' on' : '')}
                onClick={() => setSec(t[0])}
              >
                <Icon n={t[1]} s={15} />
                {t[0]}
              </button>
            ))}
          </div>

          {sec === '조직' && (
            <div className="fade-up" style={{ maxWidth: 640 }}>
              <Panel title="회사 정보">
                <div className="col gap16">
                  <div className="two-eq" style={{ gap: 14 }}>
                    <div className="field">
                      <label>회사명</label>
                      <input className="input" defaultValue={org.name} />
                    </div>
                    <div className="field">
                      <label>사업자등록번호</label>
                      <input className="input mono" defaultValue={org.bizno} />
                    </div>
                  </div>
                  <div className="two-eq" style={{ gap: 14 }}>
                    <div className="field">
                      <label>대표 도메인</label>
                      <input className="input mono" defaultValue={org.domain} />
                    </div>
                    <div className="field">
                      <label>담당자</label>
                      <input className="input" defaultValue={org.manager} />
                    </div>
                  </div>
                  <div className="field">
                    <label>조직 ID</label>
                    <div className="input-group">
                      <span className="ig-icon">
                        <Icon n="lock" s={15} />
                      </span>
                      <input
                        className="mono"
                        defaultValue={org.orgId}
                        readOnly
                        style={{ color: 'var(--n-500)' }}
                      />
                      <button
                        className="btn btn-quiet btn-sm"
                        style={{ marginRight: 5 }}
                        onClick={() => copyText(org.orgId, '조직 ID가 복사되었습니다')}
                      >
                        <Icon n="copy" s={14} />
                      </button>
                    </div>
                    <span className="hint">위젯 요청·감사 로그에 사용되는 고유 식별자</span>
                  </div>
                </div>
              </Panel>
            </div>
          )}

          {sec === '정책' && (
            <div className="fade-up" style={{ maxWidth: 640 }}>
              <Panel title="규제 프리셋" sub="변경 시 전체 애플리케이션에 영향을 줍니다." pad={false}>
                <table className="tbl">
                  <thead>
                    <tr>
                      <th>프리셋</th>
                      <th>버전</th>
                      <th className="r">상태</th>
                    </tr>
                  </thead>
                  <tbody>
                    {presets.map((p) => (
                      <tr key={p.name}>
                        <td className="t-strong">{p.name}</td>
                        <td className="mono muted" style={{ fontSize: 12.5 }}>
                          {p.ver}
                        </td>
                        <td className="r">
                          <Badge tone={p.state === '적용' ? 'ok' : 'warn'} dot>
                            {p.state}
                          </Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Panel>
            </div>
          )}

          {sec === '알림' && (
            <div className="fade-up" style={{ maxWidth: 640 }}>
              <Panel title="알림">
                <div className="col gap2">
                  {NOTI_ROWS.map((r, i) => (
                    <div
                      key={r.key}
                      className="row spread"
                      style={{
                        padding: '11px 0',
                        borderBottom: i < 3 ? '1px solid var(--n-150)' : 'none',
                      }}
                    >
                      <span style={{ fontSize: 13.5, fontWeight: 550 }}>{r.label}</span>
                      <Toggle
                        on={notifications[r.key]}
                        onChange={(v) => setNotification(r.key, v)}
                      />
                    </div>
                  ))}
                </div>
              </Panel>
            </div>
          )}
        </>
      )}
    </div>
  );
}
