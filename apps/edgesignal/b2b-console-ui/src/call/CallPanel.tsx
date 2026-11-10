import { Fragment, useEffect, useRef } from 'react';
import { hhmm } from '../report.ts';
import { SectorLogo } from '../components/SectorLogo';
import type { History } from './history.ts';
import { etfByCode, type Phase } from './useCallListener.ts';

/** 오른쪽 통화 내역 — 통화 → 종목 구간 → 문장. 버전 2 답변은 질문 바로 아래에 붙는다. */
export function CallPanel(p: { history: History; phase: Phase; ver: 1 | 2; onPick: (code: string) => void; onClose: () => void }) {
  const end = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  // 맨 아래를 보고 있을 때만 따라 내려간다 — 지난 내역을 올려 읽는 중이면 끌어내리지 않는다.
  useEffect(() => {
    if (stick.current) end.current?.scrollIntoView({ block: 'end' });
  }, [p.history]);

  const { segments, partial } = p.history;
  return (
    <section className="call-panel">
      <div className="cp-head">
        <span className="t-label">통화 내역</span>
        {p.phase !== 'live' && (
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
