import { useEffect, useRef, useState } from 'react';
import { QUEUE, REPORTS } from '../data/reports.ts';
import { transcribeKeywords } from './keywords.ts';
import { startTranscription, type TranscriptSession } from './realtime.ts';
import { roomSender } from './room.ts';

// 휴대폰 전화 화면(#/phone/{방}, ALPHA-1490). 콘솔의 [휴대폰 연결] QR 로 연다.
// [전화]를 누르면 휴대폰 마이크로 듣고, 받아 적은 말을 그 방으로 보낸다 — 탭·내역·답변은 콘솔에 뜬다.

const KEYWORDS = transcribeKeywords([...REPORTS, ...QUEUE].map((r) => r.name));
const CUSTOMER = { name: '김지현 고객님', phone: '010-••••-2847' };

function mmss(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
}

function PhoneIcon() {
  return (
    <svg width="30" height="30" viewBox="0 0 24 24" fill="currentColor">
      <path d="M6.6 10.8a15.1 15.1 0 0 0 6.6 6.6l2.2-2.2c.3-.3.7-.4 1-.2 1.1.4 2.3.6 3.6.6.6 0 1 .4 1 1V20c0 .6-.4 1-1 1A17 17 0 0 1 3 4c0-.6.4-1 1-1h3.5c.6 0 1 .4 1 1 0 1.3.2 2.5.6 3.6.1.3 0 .7-.2 1z" />
    </svg>
  );
}

export function PhoneApp({ room }: { room: string }) {
  const [phase, setPhase] = useState<'idle' | 'connecting' | 'live'>('idle');
  const [error, setError] = useState('');
  const [startedAt, setStartedAt] = useState(0);
  const [now, setNow] = useState(Date.now);
  const [heard, setHeard] = useState('');
  const session = useRef<TranscriptSession | null>(null);
  const sender = useRef<ReturnType<typeof roomSender> | null>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    // 화면을 닫거나 다른 앱으로 넘어가 페이지가 내려가면 언마운트가 돌지 않는다 — 종료를 비콘으로 남긴다.
    const leave = () => {
      if (!session.current) return;
      session.current.stop();
      session.current = null;
      navigator.sendBeacon(`/api/call/room/${room}`, JSON.stringify({ events: [{ type: 'stop' }] }));
    };
    addEventListener('pagehide', leave);
    return () => {
      mounted.current = false;
      removeEventListener('pagehide', leave);
      session.current?.stop();
      sender.current?.push({ type: 'stop' });
      void sender.current?.close();
    };
  }, [room]);

  useEffect(() => {
    if (phase !== 'live') return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [phase]);

  const call = async () => {
    setPhase('connecting');
    setError('');
    setHeard('');
    const out = roomSender(room, setError);
    sender.current = out;
    // 콘솔이 통화 시작을 먼저 알아야 뒤따르는 조각을 이 통화에 붙인다.
    out.push({ type: 'start' });
    try {
      const s = await startTranscription(KEYWORDS, {
        onDelta: (_id, delta) => {
          out.push({ type: 'delta', text: delta });
          setHeard((h) => (h + delta).slice(-120));
        },
        onCompleted: () => {},
        // 전사 연결이 끊기면 통화를 끝내 콘솔이 "통화 중"으로 남지 않게 한다.
        onError: (message) => {
          setError(message);
          if (sender.current === out) void hangUp();
        },
      });
      if (!mounted.current) return s.stop();
      session.current = s;
      setStartedAt(Date.now());
      setNow(Date.now());
      setPhase('live');
    } catch (err) {
      out.push({ type: 'stop' });
      void out.close();
      sender.current = null;
      if (!mounted.current) return;
      setPhase('idle');
      setError(err instanceof DOMException && err.name === 'NotAllowedError' ? '마이크 권한이 필요합니다.' : String((err as Error).message ?? err));
    }
  };

  // 이 통화의 보내기만 닫는다 — 닫는 사이 다시 [통화 시작]을 눌러 생긴 새 보내기를 건드리지 않게.
  const hangUp = async () => {
    const out = sender.current;
    sender.current = null;
    session.current?.stop();
    session.current = null;
    setPhase('idle');
    if (!out) return;
    out.push({ type: 'stop' });
    await out.close();
  };

  const live = phase === 'live';
  // ALPHA-1487 의 심플한 전화 화면 그대로 — 탭·답변은 콘솔에 뜨므로 여기엔 통화만 둔다.
  return (
    <div className="ph">
      <div className="ph-call">
        <div className="ph-link">
          <span className="ph-dot" />
          콘솔과 연결됨 · 방 {room.slice(0, 4)}
        </div>
        <div className="ph-who">
          <div className="ph-avatar">김</div>
          <div className="ph-name">{CUSTOMER.name}</div>
          <div className="ph-sub">{live ? mmss(now - startedAt) : phase === 'connecting' ? '연결 중… 잠시만 기다려 주세요' : CUSTOMER.phone}</div>
        </div>
        <div className="ph-live">
          {live && (
            <>
              <div className="ph-listen">
                <span className="ph-eq">
                  <i />
                  <i />
                  <i />
                </span>
                지금 말씀하세요 · 탭은 콘솔에 떠요
              </div>
              <div className="ph-lines">
                <p>{heard.length > 60 ? '…' + heard.slice(-60) : heard}</p>
              </div>
            </>
          )}
          {error && <div className="ph-error">{error}</div>}
        </div>
        <div className="ph-actions">
          {live ? (
            <button type="button" className="ph-btn end" onClick={hangUp} aria-label="통화 종료">
              <PhoneIcon />
            </button>
          ) : (
            <button type="button" className="ph-btn start" onClick={call} disabled={phase === 'connecting'} aria-label="전화">
              <PhoneIcon />
            </button>
          )}
          <div className="ph-btn-label">{live ? '종료' : '통화 시작'}</div>
        </div>
        <div className="ph-foot">EDGE 제안용 데모</div>
      </div>
    </div>
  );
}
