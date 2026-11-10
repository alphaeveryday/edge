import { useEffect, useRef, useState } from 'react';
import { Delta, Toaster } from 'ui-kit';
import { QUEUE, REPORTS } from '../data/reports.ts';
import { DEMO_BASE, direction, type FeedItem, type QueuedReport } from '../report.ts';
import { ReportDetail } from '../components/ReportDetail';
import { SectorLogo } from '../components/SectorLogo';
import { findEtf, transcribeKeywords } from './keywords.ts';
import { startTranscription, type TranscriptSession } from './realtime.ts';
import { streamSuggestion } from './suggest.ts';

// 모바일 통화 화면(#/call) — 통화 중 들린 종목 이름으로 리포트를 바로 연다.
//   버전 1(#/call): "○○로 이동할까요?" 카드
//   버전 2(#/call?v=2): 같은 카드에 고객에게 할 말을 실시간으로 만들어 보여 준다
// 카드의 [이번 통화 동안 닫기]는 통화가 끝날 때까지 어떤 카드도 다시 띄우지 않는다.

const ETFS: QueuedReport[] = [...REPORTS, ...QUEUE];
const byCode = (code: string) => ETFS.find((r) => r.code === code)!;
const KEYWORDS = transcribeKeywords(ETFS.map((r) => r.name));
/** 이름이 다 들릴 때까지 잠깐 기다린다 — "K방산"까지만 듣고 "K방산&우주"를 SOL K방산으로 띄우지 않게. */
const DETECT_SETTLE_MS = 400;
/** 버전 2: 고객 말이 이만큼 늘면 답변을 다시 만든다 */
const RESUGGEST_CHARS = 12;
const CUSTOMER = { name: '김지현 고객님', phone: '010-••••-2847' };

type Phase = 'idle' | 'connecting' | 'live';

function version(): 1 | 2 {
  return /[?&]v=2/.test(location.hash) ? 2 : 1;
}

function mmss(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
}

function PhoneIcon({ size = 30 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor">
      <path d="M6.6 10.8a15.1 15.1 0 0 0 6.6 6.6l2.2-2.2c.3-.3.7-.4 1-.2 1.1.4 2.3.6 3.6.6.6 0 1 .4 1 1V20c0 .6-.4 1-1 1A17 17 0 0 1 3 4c0-.6.4-1 1-1h3.5c.6 0 1 .4 1 1 0 1.3.2 2.5.6 3.6.1.3 0 .7-.2 1z" />
    </svg>
  );
}

/** 리포트 화면용 FeedItem — 수신 시각은 데모 기준 시각으로 고정한다. */
function asItem(r: QueuedReport): FeedItem {
  return { ...r, id: 'call-' + r.code, recv: DEMO_BASE, read: true, fresh: false };
}

export function CallApp() {
  const [ver, setVer] = useState(version);
  const [phase, setPhase] = useState<Phase>('idle');
  const [error, setError] = useState('');
  const [startedAt, setStartedAt] = useState(0);
  const [now, setNow] = useState(Date.now);
  const [lines, setLines] = useState<{ id: string; text: string }[]>([]);
  const [card, setCard] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const [viewing, setViewing] = useState<string | null>(null);
  const [suggestion, setSuggestion] = useState({ text: '', busy: false, failed: false });

  const session = useRef<TranscriptSession | null>(null);
  /** 아직 종목을 찾지 않은 전사 꼬리 — 종목을 찾으면 비운다 */
  const pending = useRef('');
  const settle = useRef<ReturnType<typeof setTimeout>>(undefined);
  /** 통화 전체 전사(답변 생성 입력) */
  const transcript = useRef('');
  const state = useRef({ card, dismissed, viewing });
  state.current = { card, dismissed, viewing };

  useEffect(() => {
    const onHash = () => setVer(version());
    addEventListener('hashchange', onHash);
    return () => removeEventListener('hashchange', onHash);
  }, []);

  useEffect(() => {
    if (phase !== 'live') return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [phase]);

  useEffect(() => () => session.current?.stop(), []);

  const detect = () => {
    const code = findEtf(pending.current);
    if (!code) {
      pending.current = pending.current.slice(-40);
      return;
    }
    pending.current = '';
    const s = state.current;
    if (s.dismissed || code === s.card || code === s.viewing) return;
    setCard(code);
  };

  const hear = (delta: string) => {
    transcript.current += delta;
    pending.current += delta;
    clearTimeout(settle.current);
    settle.current = setTimeout(detect, DETECT_SETTLE_MS);
  };

  const start = async () => {
    setPhase('connecting');
    setError('');
    setLines([]);
    setCard(null);
    setDismissed(false);
    setViewing(null);
    transcript.current = '';
    pending.current = '';
    try {
      session.current = await startTranscription(KEYWORDS, {
        onDelta: (id, delta) => {
          hear(delta);
          setLines((prev) => {
            const i = prev.findIndex((l) => l.id === id);
            if (i < 0) return [...prev, { id, text: delta }].slice(-6);
            const next = prev.slice();
            next[i] = { id, text: next[i].text + delta };
            return next;
          });
        },
        onCompleted: (id, text) => {
          transcript.current += '\n';
          setLines((prev) => prev.map((l) => (l.id === id ? { id, text } : l)));
        },
        onError: (message) => setError(message),
      });
      setStartedAt(Date.now());
      setNow(Date.now());
      setPhase('live');
    } catch (err) {
      setPhase('idle');
      setError(err instanceof DOMException && err.name === 'NotAllowedError' ? '마이크 권한이 필요합니다.' : String((err as Error).message ?? err));
    }
  };

  const hangUp = () => {
    session.current?.stop();
    session.current = null;
    clearTimeout(settle.current);
    setPhase('idle');
    setCard(null);
    setViewing(null);
  };

  const go = () => {
    setViewing(card);
    setCard(null);
  };

  return (
    <div className="phone">
      {viewing && phase === 'live' ? (
        <div className="call-report">
          <button type="button" className="call-pill" onClick={() => setViewing(null)}>
            <PhoneIcon size={14} />
            <span>통화 중 {mmss(now - startedAt)}</span>
            <span className="call-pill-back">통화 화면</span>
          </button>
          <div className="call-report-body">
            <ReportDetail key={viewing} report={asItem(byCode(viewing))} />
          </div>
        </div>
      ) : (
        <CallScreen
          phase={phase}
          ver={ver}
          error={error}
          elapsed={now - startedAt}
          lines={lines}
          onStart={start}
          onHangUp={hangUp}
        />
      )}

      {card && phase === 'live' && (
        <Card
          key={card}
          report={byCode(card)}
          ver={ver}
          transcript={transcript}
          suggestion={suggestion}
          setSuggestion={setSuggestion}
          onGo={go}
          onDismiss={() => {
            setCard(null);
            setDismissed(true);
          }}
        />
      )}
      <Toaster />
    </div>
  );
}

function CallScreen(p: {
  phase: Phase;
  ver: 1 | 2;
  error: string;
  elapsed: number;
  lines: { id: string; text: string }[];
  onStart: () => void;
  onHangUp: () => void;
}) {
  const live = p.phase === 'live';
  return (
    <div className="call">
      {!live && (
        <div className="ver-switch" role="tablist">
          {([1, 2] as const).map((v) => (
            <a key={v} role="tab" aria-selected={p.ver === v} className={p.ver === v ? 'on' : ''} href={v === 1 ? '#/call' : '#/call?v=2'}>
              {v === 1 ? '버전 1 · 종목 이동' : '버전 2 · 답변 추천'}
            </a>
          ))}
        </div>
      )}
      <div className="call-who">
        <div className="call-avatar">김</div>
        <div className="call-name">{CUSTOMER.name}</div>
        <div className="call-sub">{live ? mmss(p.elapsed) : p.phase === 'connecting' ? '연결 중…' : CUSTOMER.phone}</div>
      </div>

      <div className="call-live">
        {live && (
          <>
            <div className="call-listen">
              <span className="eq">
                <i />
                <i />
                <i />
              </span>
              종목 이름을 듣고 있어요
            </div>
            <div className="call-lines">
              {/* 전사는 통화 내내 한 줄로 길어진다 — 최근 말만 보여 준다. */}
              {p.lines.slice(-2).map((l) => (
                <p key={l.id}>{l.text.length > 60 ? '…' + l.text.slice(-60) : l.text}</p>
              ))}
            </div>
          </>
        )}
        {p.error && <div className="call-error">{p.error}</div>}
      </div>

      <div className="call-actions">
        {live ? (
          <button type="button" className="call-btn end" onClick={p.onHangUp} aria-label="통화 종료">
            <PhoneIcon />
          </button>
        ) : (
          <button type="button" className="call-btn start" onClick={p.onStart} disabled={p.phase === 'connecting'} aria-label="통화 시작">
            <PhoneIcon />
          </button>
        )}
        <div className="call-btn-label">{live ? '종료' : '통화 시작'}</div>
      </div>
      <div className="call-foot">EDGE 제안용 데모 · 리포트 수치는 가상 데이터입니다</div>
    </div>
  );
}

type Suggestion = { text: string; busy: boolean; failed: boolean };

function Card(p: {
  report: QueuedReport;
  ver: 1 | 2;
  transcript: React.RefObject<string>;
  suggestion: Suggestion;
  setSuggestion: React.Dispatch<React.SetStateAction<Suggestion>>;
  onGo: () => void;
  onDismiss: () => void;
}) {
  const r = p.report;
  const { setSuggestion } = p;

  // 버전 2: 카드가 뜨면 바로 한 번, 이후 고객 말이 늘 때마다 다시 만든다. 한 번에 하나만 돌리고,
  // 도는 사이 말이 늘었으면 끝난 뒤 이어서 한 번 더 돈다(도중에 끊으면 말하는 내내 아무것도 안 보인다).
  useEffect(() => {
    if (p.ver !== 2) return;
    const ac = new AbortController();
    let usedLen = -RESUGGEST_CHARS;
    let running = false;
    const run = async () => {
      const text = p.transcript.current ?? '';
      if (running || text.length - usedLen < RESUGGEST_CHARS) return;
      running = true;
      usedLen = text.length;
      setSuggestion((s) => ({ ...s, busy: true, failed: false }));
      try {
        await streamSuggestion(text.slice(-600), r, (t) => setSuggestion({ text: t, busy: true, failed: false }), ac.signal);
        setSuggestion((s) => ({ ...s, busy: false }));
      } catch {
        if (!ac.signal.aborted) setSuggestion((s) => ({ ...s, busy: false, failed: !s.text }));
      } finally {
        running = false;
      }
    };
    setSuggestion({ text: '', busy: true, failed: false });
    run();
    const t = setInterval(run, 1200);
    return () => {
      ac.abort();
      clearInterval(t);
    };
  }, [p.ver, r, p.transcript, setSuggestion]);

  return (
    <div className="sheet" role="dialog" aria-label={`${r.name}로 이동할까요?`}>
      <div className="sheet-etf">
        <SectorLogo theme={r.theme} size={40} />
        <div className="sheet-etf-name">
          <b>{r.name}</b>
          <span>{r.code}</span>
        </div>
        <Delta direction={direction(r.pct)} pct={Math.abs(r.pct)} style={{ fontSize: 17 }} />
      </div>
      <div className="sheet-q">{r.name}로 이동할까요?</div>

      {p.ver === 2 && (
        <div className="say">
          <div className="say-head">고객에게 이렇게 말해 보세요</div>
          {p.suggestion.failed ? (
            <p className="say-fallback">{r.say}</p>
          ) : (
            <p>
              {p.suggestion.text || <span className="say-wait">답변을 만들고 있어요</span>}
              {p.suggestion.busy && <span className="caret" />}
            </p>
          )}
        </div>
      )}

      <button type="button" className="sheet-go" onClick={p.onGo}>
        이동
      </button>
      <button type="button" className="sheet-close" onClick={p.onDismiss}>
        이번 통화 동안 닫기
      </button>
    </div>
  );
}
