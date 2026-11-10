import { Fragment, useEffect, useRef } from 'react';
import { hhmm } from '../report.ts';
import { SectorLogo } from '../components/SectorLogo';
import type { History } from './history.ts';
import { etfByCode, type Phase } from './useCallListener.ts';

function mmss(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
}

/**
 * 통화 내역 — 통화 → 종목 구간 → 문장. 버전 2 답변은 질문 바로 아래에 붙는다.
 * 넓은 화면에서는 오른쪽 패널, 휴대폰에서는 화면 전체 채팅창(call.css 의 760px 이하 규칙)이다.
 * hidden 은 휴대폰에서 리포트를 보느라 채팅창을 접은 상태다.
 */
export function CallPanel(p: {
  history: History;
  phase: Phase;
  ver: 1 | 2;
  elapsed: number;
  hidden: boolean;
  error: string;
  onPick: (code: string) => void;
  onStop: () => void;
  onClose: () => void;
}) {
  const end = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  // 맨 아래를 보고 있을 때만 따라 내려간다 — 지난 내역을 올려 읽는 중이면 끌어내리지 않는다.
  useEffect(() => {
    if (stick.current) end.current?.scrollIntoView({ block: 'end' });
  }, [p.history]);

  const { segments, partial } = p.history;
  return (
    <section className={`call-panel${p.hidden ? ' chat-hidden' : ''}`}>
      <div className="cp-head">
        <span className="t-label">통화 내역</span>
        {p.phase === 'live' && (
          <span className="cp-live">
            <span className="rec" />
            통화 중 {mmss(p.elapsed)} · {p.ver === 1 ? '종목 이동' : '답변 추천'}
            <button type="button" className="cp-stop" onClick={p.onStop}>
              종료
            </button>
          </span>
        )}
        {/* 연결 중에 닫으면 휴대폰에서는 종료 버튼이 사라진다 — 듣지 않을 때만 닫을 수 있다. */}
        {p.phase === 'idle' && (
          <button type="button" className="cp-close" onClick={p.onClose} aria-label="통화 내역 닫기">
            ×
          </button>
        )}
      </div>
      <div
        className="cp-body"
        ref={box}
        onScroll={(e) => {
          const el = e.currentTarget;
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
        }}
      >
        {p.error && <div className="cp-error">{p.error}</div>}
        {segments.length === 0 && <div className="cp-empty">통화를 들으면 여기에 내용이 쌓여요.</div>}
        {segments.map((seg, i) => {
          const r = seg.code ? etfByCode(seg.code) : undefined;
          const newCall = i === 0 || segments[i - 1].call !== seg.call;
          return (
            <Fragment key={`${seg.call}-${i}`}>
              {newCall && (
                <div className="cp-call">
                  통화 {seg.call} · {hhmm(seg.at)} 시작
                </div>
              )}
              <div className="cp-seg">
                {r ? (
                  <button type="button" className="cp-topic" onClick={() => p.onPick(r.code)} title="이 리포트 열기">
                    <SectorLogo theme={r.theme} size={20} />
                    <b>{r.name}</b>
                    <span>{hhmm(seg.at)}</span>
                    <em className="cp-open">리포트 보기</em>
                  </button>
                ) : (
                  seg.sentences.length > 0 && <div className="cp-topic none">종목 언급 전</div>
                )}
                {seg.sentences.map((s) => (
                  <div key={s.id} className={`cp-line${s.question ? ' q' : ''}`}>
                    <span className="cp-time">{hhmm(s.at)}</span>
                    <div className="cp-text">
                      {s.text}
                      {s.answer !== undefined && (
                        <div className={`cp-answer${s.fallback ? ' fallback' : ''}`}>
                          <span className="cp-answer-head">{s.fallback ? '리포트 고객 안내' : '추천 답변'}</span>
                          {s.answer || <span className="cp-wait">만드는 중…</span>}
                          {s.answering && <span className="caret" />}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </Fragment>
          );
        })}
        {p.phase === 'live' && partial && <div className="cp-partial">{partial}</div>}
        <div ref={end} />
      </div>
    </section>
  );
}
