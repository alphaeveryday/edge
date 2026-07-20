/* 위젯 탭 — 임베드·디자인 토큰·미리보기 + 위젯 키 관리.
 * 키는 applications 도메인 hook(useWidgetKeys)으로 다룬다 (mock 직접 import 없음).
 */
import { useState } from 'react';
import { Badge, Icon, Modal, Panel, Segmented, Toggle, copyText, toast } from '../../components';
import { useWidgetKeys } from '../../domains/applications';

const SNIPPET = `<script src="https://cdn.edge.io/widget.js"
  data-key="pk_live_3f9a…d21"
  data-app="app_mts"></script>`;

const COLORS = ['#27468F', '#0F766E', '#7C3AED', '#B91C1C', '#111827'];

function EmbedPanel() {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    copyText(SNIPPET, '임베드 코드가 복사되었습니다');
    setCopied(true);
    setTimeout(() => setCopied(false), 1400);
  };
  return (
    <Panel
      title="JS 1줄 임베드"
      actions={
        <button className="btn btn-ghost btn-sm" onClick={copy}>
          <Icon n={copied ? 'check' : 'copy'} s={15} />
          {copied ? '복사됨' : '복사'}
        </button>
      }
    >
      <pre className="code-block">{SNIPPET}</pre>
      <div className="muted" style={{ fontSize: 11.5, marginTop: 10 }}>
        npm: <span className="kbd">@edge/widget</span>
      </div>
    </Panel>
  );
}

function DesignPanel({
  color,
  setColor,
  radius,
  setRadius,
  dark,
  setDark,
}: {
  color: string;
  setColor: (v: string) => void;
  radius: number;
  setRadius: (v: number) => void;
  dark: boolean;
  setDark: (v: boolean) => void;
}) {
  return (
    <Panel title="화이트라벨 디자인 토큰">
      <div className="col gap28">
        <div className="field">
          <label>Primary Color</label>
          <div className="row gap8">
            {COLORS.map((c) => (
              <button
                key={c}
                className="swatch"
                onClick={() => setColor(c)}
                style={{
                  width: 30,
                  height: 30,
                  borderRadius: 8,
                  background: c,
                  outline: color === c ? '2px solid var(--n-900)' : 'none',
                  outlineOffset: 2,
                }}
                aria-label={c}
              />
            ))}
          </div>
        </div>
        <div className="field">
          <label>
            Corner Radius{' '}
            <span className="num" style={{ color: 'var(--p-700)', fontWeight: 700 }}>
              {radius}
            </span>
          </label>
          <input
            className="rng"
            type="range"
            min={0}
            max={16}
            value={radius}
            onChange={(e) => setRadius(Number(e.target.value))}
          />
        </div>
        <div className="row spread" style={{ alignItems: 'center' }}>
          <label style={{ margin: 0, fontSize: 13.5, fontWeight: 600, color: 'var(--n-700)' }}>
            다크 모드
          </label>
          <Toggle on={dark} onChange={setDark} />
        </div>
      </div>
    </Panel>
  );
}

function PreviewPanel({ color, radius, dark }: { color: string; radius: number; dark: boolean }) {
  const bg = dark ? '#111827' : '#fff';
  const fg = dark ? '#f3f4f6' : 'var(--n-900)';
  const sub = dark ? '#9ca3af' : 'var(--n-500)';
  const border = dark ? '#374151' : 'var(--n-150)';
  return (
    <Panel title="미리보기">
      <div
        className="widget-preview"
        style={{
          background: bg,
          color: fg,
          border: `1px solid ${border}`,
          borderRadius: 14,
          padding: 16,
        }}
      >
        <div className="row gap8" style={{ alignItems: 'center', marginBottom: 10 }}>
          <span
            style={{
              fontSize: 11,
              fontWeight: 700,
              color: '#fff',
              background: color,
              borderRadius: 6,
              padding: '2px 7px',
            }}
          >
            영향도 72
          </span>
          <span style={{ fontSize: 11, color: sub }}>실적 · 3분 전</span>
        </div>
        <div style={{ fontSize: 14.5, fontWeight: 700, color: fg, lineHeight: 1.4 }}>
          삼성전자, 3분기 잠정 실적 공시
        </div>
        <div style={{ fontSize: 12.5, color: sub, marginTop: 6, lineHeight: 1.5 }}>
          반도체 업황 회복 신호로 <b style={{ color: fg }}>KODEX 반도체</b> 관련 ETF에 긍정적
          영향이 예상됩니다.
        </div>
        <div className="row gap8" style={{ marginTop: 12 }}>
          <button
            style={{
              fontSize: 12,
              fontWeight: 600,
              color: '#fff',
              background: color,
              border: 'none',
              borderRadius: radius,
              padding: '6px 12px',
            }}
          >
            자세히 보기
          </button>
          <button
            style={{
              fontSize: 12,
              fontWeight: 600,
              color,
              background: 'transparent',
              border: `1px solid ${color}`,
              borderRadius: radius,
              padding: '6px 12px',
            }}
          >
            관련 ETF
          </button>
        </div>
        <div
          style={{
            fontSize: 10.5,
            color: sub,
            marginTop: 12,
            paddingTop: 10,
            borderTop: `1px dashed ${border}`,
          }}
        >
          투자 참고 정보이며 투자 권유가 아닙니다. (면책 v4)
        </div>
      </div>
    </Panel>
  );
}

function KeysPanel() {
  const { keys, issueKey, regenerateKey, revokeKey } = useWidgetKeys();

  const [issueOpen, setIssueOpen] = useState(false);
  const [label, setLabel] = useState('');
  const [env, setEnv] = useState('test');
  const [scope, setScope] = useState('analysis:read');

  const submitIssue = async () => {
    if (!label.trim()) return;
    await issueKey({ label: label.trim(), env, scope });
    setIssueOpen(false);
    setLabel('');
    toast('키가 발급되었습니다 · 전체 값은 지금만 표시됩니다');
  };

  const regen = async (k: string) => {
    await regenerateKey(k);
    toast('키가 재발급되었습니다');
  };

  const revoke = async (k: string, lbl: string) => {
    await revokeKey(k);
    toast(`${lbl} 키가 폐기되었습니다`, 'bad');
  };

  return (
    <Panel
      title="위젯 키"
      pad={false}
      actions={
        <button className="btn btn-pri btn-sm" onClick={() => setIssueOpen(true)}>
          <Icon n="plus" s={15} />
          키 발급
        </button>
      }
    >
      <div className="tbl-scroll">
        <table className="tbl hover">
          <thead>
            <tr>
              <th>키</th>
              <th>라벨</th>
              <th>스코프</th>
              <th>환경</th>
              <th>최근 사용</th>
              <th className="r">관리</th>
            </tr>
          </thead>
          <tbody>
            {keys.map((k) => (
              <tr key={k.key}>
                <td>
                  <div className="row gap6" style={{ alignItems: 'center' }}>
                    <span className="mono" style={{ fontSize: 12 }}>
                      {k.key}
                    </span>
                    <button
                      className="btn btn-quiet btn-sm"
                      onClick={() => copyText(k.key, '키가 복사되었습니다')}
                      aria-label="키 복사"
                    >
                      <Icon n="copy" s={13} />
                    </button>
                  </div>
                </td>
                <td style={{ fontSize: 13 }}>{k.label}</td>
                <td className="mono" style={{ fontSize: 12, color: 'var(--n-500)' }}>
                  {k.scope}
                </td>
                <td>
                  <Badge tone={k.env === 'live' ? 'prod' : 'stg'} dot>
                    {k.env}
                  </Badge>
                </td>
                <td className="muted" style={{ fontSize: 12.5 }}>
                  {k.used}
                </td>
                <td className="r">
                  <div className="row gap6" style={{ justifyContent: 'flex-end' }}>
                    <button
                      className="btn btn-quiet btn-sm"
                      onClick={() => regen(k.key)}
                      title="재발급"
                    >
                      <Icon n="refresh" s={14} />
                      재발급
                    </button>
                    <button
                      className="btn btn-quiet btn-sm"
                      style={{ color: 'var(--bad)' }}
                      onClick={() => revoke(k.key, k.label)}
                      title="폐기"
                    >
                      <Icon n="trash" s={14} />
                      폐기
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="info-strip">
        <Icon n="lock" s={15} />
        키는 발급 직후 한 번만 전체 노출됩니다. 안전한 곳에 보관하세요.
      </div>

      {issueOpen && (
        <Modal
          title="키 발급"
          onClose={() => setIssueOpen(false)}
          footer={
            <>
              <button className="btn btn-ghost" onClick={() => setIssueOpen(false)}>
                취소
              </button>
              <button className="btn btn-pri" disabled={!label.trim()} onClick={submitIssue}>
                <Icon n="key" s={15} />
                발급
              </button>
            </>
          }
        >
          <div className="col gap18">
            <div className="field">
              <label>라벨</label>
              <input
                className="input"
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                placeholder="MTS · 운영"
                autoFocus
              />
            </div>
            <div className="field">
              <label>환경</label>
              <Segmented
                opts={[
                  { v: 'test', l: 'test' },
                  { v: 'live', l: 'live' },
                ]}
                value={env}
                onChange={setEnv}
                block
              />
            </div>
            <div className="field">
              <label>스코프</label>
              <Segmented
                opts={['analysis:read', 'analysis:read, widget']}
                value={scope}
                onChange={setScope}
                block
              />
            </div>
          </div>
        </Modal>
      )}
    </Panel>
  );
}

export function WidgetTab() {
  const [color, setColor] = useState(COLORS[0]);
  const [radius, setRadius] = useState(8);
  const [dark, setDark] = useState(false);

  return (
    <div>
      <div className="two-col">
        <div className="col gap20">
          <EmbedPanel />
          <DesignPanel
            color={color}
            setColor={setColor}
            radius={radius}
            setRadius={setRadius}
            dark={dark}
            setDark={setDark}
          />
        </div>
        <PreviewPanel color={color} radius={radius} dark={dark} />
      </div>

      <div className="sec-gap">
        <KeysPanel />
      </div>
    </div>
  );
}
