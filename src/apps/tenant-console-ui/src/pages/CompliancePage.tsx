/* 컴플라이언스 페이지 — compliance 도메인 hook 만 의존한다 (mock 직접 import 없음). */
import { useRef, useState } from 'react';
import { Badge, Icon, PageHeader, Panel, toast } from '../components';
import { useCompliance } from '../domains/compliance';

export function CompliancePage() {
  const { disclaimer, keywords, blockedToday, loading, error, publish, addKeyword, removeKeyword } =
    useCompliance();

  // 면책 문구 textarea 는 비제어(defaultValue)다 — 발행 시 현재 값을 ref 로 읽는다.
  const textRef = useRef<HTMLTextAreaElement>(null);
  const [nw, setNw] = useState('');

  const add = () => {
    const word = nw.trim();
    if (!word) return;
    addKeyword(word);
    setNw('');
    toast(`‘${word}’ 차단 단어 추가됨`);
  };

  const onPublish = () => {
    const text = textRef.current?.value ?? '';
    publish(text);
    toast('면책 문구 v5가 발행되었습니다');
  };

  return (
    <div>
      <PageHeader
        title="컴플라이언스"
        desc="면책 문구 편집과 키워드 블랙리스트(리딩·추천성 표현 차단)."
        actions={
          <button className="btn btn-pri" onClick={onPublish}>
            <Icon n="check" s={16} />
            발행
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
              컴플라이언스 정보를 불러오지 못했습니다
            </div>
            <div className="muted" style={{ fontSize: 12.5, marginTop: 4 }}>
              {error.message}
            </div>
          </div>
        </Panel>
      ) : loading || !disclaimer ? (
        <Panel>
          <div className="skel-bar" style={{ height: 18, width: '100%' }} />
        </Panel>
      ) : (
        <div className="two-col">
          <Panel title="면책 문구" sub="V4 · 초안" actions={<Badge tone="info">증권사 프리셋</Badge>}>
            <textarea
              ref={textRef}
              className="input"
              defaultValue={disclaimer.text}
              style={{ minHeight: 150, fontSize: 13.5 }}
            />
            <div className="row spread" style={{ marginTop: 12 }}>
              <label className="row gap8" style={{ fontSize: 13, fontWeight: 550, cursor: 'pointer' }}>
                <span className="cbox on">
                  <Icon n="check" s={12} sw={2.8} />
                </span>
                모든 분석 출력 하단에 자동 부착
              </label>
              <span className="badge badge-neutral">버전 {disclaimer.version}</span>
            </div>
          </Panel>

          <Panel title="키워드 블랙리스트" sub="포함 시 출력 자동 보류">
            <div className="input-group" style={{ marginBottom: 14 }}>
              <span className="ig-icon">
                <Icon n="alert" s={15} />
              </span>
              <input
                placeholder="차단 단어 추가"
                value={nw}
                onChange={(e) => setNw(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && add()}
              />
              <button className="btn btn-pri btn-sm" style={{ margin: 4 }} onClick={add}>
                추가
              </button>
            </div>
            <div className="row gap8 wrap">
              {keywords.map((k) => (
                <span
                  key={k}
                  className="tag-chip"
                  style={{ background: 'var(--bad-bg)', borderColor: 'var(--bad-bd)', color: 'var(--bad)' }}
                >
                  {k}
                  <span
                    className="chip-x"
                    style={{ color: 'var(--bad)' }}
                    onClick={() => removeKeyword(k)}
                  >
                    <Icon n="x" s={13} />
                  </span>
                </span>
              ))}
            </div>
            <div
              className="info-strip"
              style={{
                marginTop: 16,
                color: 'var(--warn)',
                background: 'var(--warn-bg)',
                borderColor: 'var(--warn-bd)',
              }}
            >
              <Icon n="alert" s={15} />
              차단 단어 포함 출력은 자동 보류되어 Audit Log에 기록됩니다. 오늘
              <b style={{ margin: '0 3px' }}>{blockedToday}건</b> 차단.
            </div>
          </Panel>
        </div>
      )}
    </div>
  );
}
