import { useEffect, useRef, useState } from 'react';
import { QUEUE, REPORTS } from '../data/reports.ts';
import type { QueuedReport } from '../report.ts';
import { EMPTY, addText, currentCode, flush, pendingTurn, setAnswer, startCall, type History } from './history.ts';
import { findEtf, transcribeKeywords } from './keywords.ts';
import { startTranscription, type TranscriptSession } from './realtime.ts';
import { readRoom } from './room.ts';
import { marketContext, reportContext, streamSuggestion } from './suggest.ts';

// 콘솔이 노트북 마이크로 상담사의 전화 통화를 듣는다. 상담사는 실제 전화로 통화하고, 이 훅은
//   - 종목 이름이 들리면 "○○로 이동할까요?" 탭(card)을 띄우고
//   - 통화 내용을 통화 → 종목 구간 → 문장 단위 내역(history)으로 쌓고
//   - 버전 2 에서는 새 질문마다 고객에게 할 답변을 만들어 그 질문에 붙인다.
// [이번 통화 동안 닫기](dismiss)는 탭만 막는다 — 내역과 버전 2 답변은 계속 쌓인다.

export const ETFS: QueuedReport[] = [...REPORTS, ...QUEUE];
export const etfByCode = (code: string) => ETFS.find((r) => r.code === code);
const KEYWORDS = transcribeKeywords(ETFS.map((r) => r.name));
/** 이름이 다 들릴 때까지 잠깐 기다린다 — "K방산"까지만 듣고 "K방산&우주"를 SOL K방산으로 띄우지 않게. */
const DETECT_SETTLE_MS = 400;
/** 질문이 없으면 말이 이만큼 멈췄을 때 그 차례에 답한다 */
const PAUSE_MS = 1500;
/** 답변이 이 안에 끝나지 않으면 끊고 리포트 고객 안내 문구로 대신한다 */
const SUGGEST_TIMEOUT_MS = 15_000;
/** 종목 이름을 찾는 최근 전사 길이 — 가장 긴 이름보다 넉넉하게 */
const RECENT_CHARS = 60;
/** 답변 생성에 넘길 최근 통화 길이 */
const SUGGEST_CONTEXT = 600;

export type Phase = 'idle' | 'connecting' | 'live';

function callText(h: History): string {
  const call = h.calls;
  const said = h.segments.filter((s) => s.call === call).flatMap((s) => s.sentences.map((x) => x.text));
  return [...said, h.partial].join(' ').slice(-SUGGEST_CONTEXT);
}

/** 휴대폰 방을 읽는 간격 — 휴대폰은 0.3초마다 올린다 */
const ROOM_POLL_MS = 600;

/**
 * selectedCode: 지금 열린 리포트(그 종목은 탭을 안 띄운다).
 * onCallStart: 휴대폰에서 통화가 시작돼 콘솔이 듣기 시작할 때(화면이 통화 내역을 연다).
 */
export function useCallListener(selectedCode: string | null, onCallStart?: () => void) {
  const [ver, setVer] = useState<1 | 2>(1);
  const [phase, setPhase] = useState<Phase>('idle');
  const [error, setError] = useState('');
  const [startedAt, setStartedAt] = useState(0);
  const [now, setNow] = useState(Date.now);
  const [history, setHistory] = useState<History>(EMPTY);
  const [card, setCard] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState(false);
  /** 말이 멈출 때마다 늘려 답변 효과를 다시 돌린다 */
  const [quiet, setQuiet] = useState(0);
  const lastHeardAt = useRef(0);
  const pauseTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  /** 연결된 휴대폰 방 — 있으면 이 콘솔은 노트북 마이크 대신 휴대폰이 보낸 말을 듣는다(ALPHA-1490) */
  const [room, setRoom] = useState<string | null>(null);
  const phaseRef = useRef(phase);
  phaseRef.current = phase;
  const onStartRef = useRef(onCallStart);
  onStartRef.current = onCallStart;

  const session = useRef<TranscriptSession | null>(null);
  /** 최근 전사 꼬리 — 비우지 않고 밀어낸다. 조각이 이름 중간에서 끊겨 와도("K방산&우" | "주는") 이어서 읽는다. */
  const recent = useRef('');
  /** 마지막으로 탭을 띄운 종목 — 그 이름이 최근 전사에 남아 있는 동안 다시 띄우지 않는다. */
  const heard = useRef<string | null>(null);
  const settle = useRef<ReturnType<typeof setTimeout>>(undefined);
  const live = useRef({ card, dismissed, selectedCode });
  live.current = { card, dismissed, selectedCode };
  // 연결 중에 화면을 떠나도 마이크·전사 세션이 남지 않게 — 연결이 늦게 끝나면 start 가 이 값을 보고 바로 끊는다.
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true; // StrictMode 는 마운트→정리→마운트를 한 번 더 돈다
    return () => {
      mounted.current = false;
      session.current?.stop();
      answering.current?.abort();
    };
  }, []);

  useEffect(() => {
    if (phase !== 'live') return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [phase]);

  const detect = () => {
    const code = findEtf(recent.current);
    // 최근 전사에서 이름이 빠지면 같은 종목도 다시 띄울 수 있다 — 이동 후 다른 리포트를 보다가 다시 나오는 경우.
    if (!code) heard.current = null;
    if (!code || code === heard.current) return;
    const s = live.current;
    if (s.dismissed || code === s.selectedCode) return;
    // 이름이 이어 들려 더 긴 이름으로 바뀌면("K방산" → "K방산&우주") 띄운 탭도 바뀐다.
    heard.current = code;
    setCard(code);
  };

  /** 새 통화 준비 — 탭·닫기·이름 꼬리를 비우고 내역에 통화 구간을 연다. */
  const beginCall = () => {
    setError('');
    setCard(null);
    setDismissed(false);
    recent.current = '';
    heard.current = null;
    setHistory((h) => startCall(h, Date.now()));
  };
  // phaseRef 를 바로 맞춘다 — 한 번에 읽은 [시작, 조각…]에서 조각이 다음 렌더를 기다리다 버려지지 않게.
  const goLive = () => {
    phaseRef.current = 'live';
    setStartedAt(Date.now());
    setNow(Date.now());
    setPhase('live');
  };
  /** 전사 조각 하나 — 내역에 쌓고, 잠깐 뒤 종목 이름을 찾는다. 노트북 마이크·휴대폰 방 어느 쪽이든 같다. */
  const hear = (delta: string) => {
    lastHeardAt.current = Date.now();
    clearTimeout(pauseTimer.current);
    pauseTimer.current = setTimeout(() => setQuiet((n) => n + 1), PAUSE_MS);
    setHistory((h) => addText(h, delta, Date.now()));
    recent.current = (recent.current + delta).slice(-RECENT_CHARS);
    clearTimeout(settle.current);
    settle.current = setTimeout(detect, DETECT_SETTLE_MS);
  };
  const endCall = () => {
    phaseRef.current = 'idle';
    answering.current?.abort();
    clearTimeout(settle.current);
    setHistory((h) => flush(h, Date.now()));
    setPhase('idle');
    setCard(null);
  };

  const start = async () => {
    setPhase('connecting');
    beginCall();
    try {
      const s = await startTranscription(KEYWORDS, {
        onDelta: (_id, delta) => hear(delta),
        onCompleted: () => {},
        onError: (message) => setError(message),
      });
      if (!mounted.current) return s.stop();
      session.current = s;
      goLive();
    } catch (err) {
      if (!mounted.current) return;
      // 연결이 안 된 통화는 내역에 빈 구간으로 남기지 않는다.
      setHistory((h) => {
        const last = h.segments[h.segments.length - 1];
        return last?.call === h.calls && !last.sentences.length ? { ...h, calls: h.calls - 1, segments: h.segments.slice(0, -1) } : h;
      });
      setPhase('idle');
      setError(err instanceof DOMException && err.name === 'NotAllowedError' ? '마이크 권한이 필요합니다.' : String((err as Error).message ?? err));
    }
  };

  const stop = () => {
    session.current?.stop();
    session.current = null;
    endCall();
  };

  // 휴대폰 방 읽기 — 순서대로 받아 시작·조각·종료를 그대로 처리한다. 방은 연결할 때 새로 만든 무작위 코드라
  // 처음부터(0) 읽는다 — 노트북 시계로 자르면 시계가 서버보다 빠를 때 앞부분을 놓친다.
  useEffect(() => {
    if (!room) return;
    let after = 0;
    let busy = false;
    let stopped = false;
    const poll = async () => {
      if (busy) return;
      busy = true;
      try {
        for (const e of await readRoom(room, after)) {
          if (stopped) return;
          after = Math.max(after, e.seq);
          if (e.type === 'start') {
            // 노트북 마이크로 듣던 중이면 끄고 휴대폰 통화로 넘어간다 — 두 소리가 한 내역에 섞이지 않게.
            session.current?.stop();
            session.current = null;
            beginCall();
            goLive();
            onStartRef.current?.();
          } else if (e.type === 'delta' && phaseRef.current === 'live') hear(e.text);
          else if (e.type === 'stop' && phaseRef.current === 'live') endCall();
        }
      } catch (err) {
        setError((err as Error).message);
      } finally {
        busy = false;
      }
    };
    const t = setInterval(poll, ROOM_POLL_MS);
    return () => {
      stopped = true;
      clearInterval(t);
    };
    // beginCall·hear·endCall 은 ref 와 상태 설정 함수만 쓴다 — 방이 바뀔 때만 다시 건다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [room]);

  // 버전 2(답변 추천): 고객 차례마다 상담사가 이어 할 말을 만든다(history.ts pendingTurn). 질문이 끝나면 바로,
  // 아니면 말이 1.5초 멈췄을 때 그 차례 전체에 한 번. 종목이 나온 구간은 그 리포트, 아니면 시장 전체 요약을 근거로 한다.
  // 한 번에 하나만 돌리고, 끝나면 내역이 바뀌어 이 효과가 다시 돌며 그사이 쌓인 차례를 고른다.
  // 실패·빈 답변은 종목 구간이면 리포트 고객 안내 문구로 대신하고, 아니면 "만들지 못했어요"로 둔다.
  const running = useRef(false);
  const answering = useRef<AbortController | null>(null);
  const historyRef = useRef(history);
  historyRef.current = history;
  useEffect(() => {
    if (ver !== 2 || phase !== 'live' || running.current) return;
    const turn = pendingTurn(history);
    if (!turn) return;
    if (!turn.asked && Date.now() - lastHeardAt.current < PAUSE_MS) return;
    const report = turn.code ? etfByCode(turn.code) : undefined;
    const context = report ? reportContext(report) : marketContext(ETFS);
    const id = turn.sentence.id;
    // 이번 통화의 직전 답변을 함께 넘겨 같은 말을 되풀이하지 않게 한다(지난 고객 답변은 넘기지 않는다).
    const cur = historyRef.current;
    const prev = cur.segments
      .filter((seg) => seg.call === cur.calls)
      .reverse()
      .flatMap((seg) => [...seg.sentences].reverse())
      .find((x) => x.answer);
    const transcript = callText(historyRef.current) + `\n[이번에 답할 고객 말] ${turn.text}` + (prev?.answer ? `\n[직전에 추천한 답변] ${prev.answer}` : '');
    running.current = true;
    setHistory((h) => setAnswer(h, id, { answer: '', answering: true }));
    const ac = new AbortController();
    answering.current = ac;
    const timer = setTimeout(() => ac.abort(), SUGGEST_TIMEOUT_MS);
    const fallback = { answer: report?.say ?? '', answering: false, fallback: true };
    streamSuggestion(transcript, context, (t) => setHistory((h) => setAnswer(h, id, { answer: t, answering: true })), ac.signal)
      .then((out) => setHistory((h) => setAnswer(h, id, out.trim() ? { answer: out, answering: false } : fallback)))
      .catch(() => setHistory((h) => setAnswer(h, id, fallback)))
      .finally(() => {
        clearTimeout(timer);
        answering.current = null;
        running.current = false;
      });
  }, [history, ver, phase, quiet]);

  // 종목 이름 놓침 방지 — 조각 단위 탐지(detect)에 더해, 끝난 문장에서 새 종목 구간이 열리면 그 구간당 한 번 탭을 띄운다.
  const alerted = useRef('');
  useEffect(() => {
    if (phase !== 'live') return;
    const code = currentCode(history);
    const key = `${history.calls}:${history.segments.length}`;
    if (!code || alerted.current === key) return;
    alerted.current = key;
    const s = live.current;
    if (s.dismissed || code === s.selectedCode || code === s.card || code === heard.current) return;
    heard.current = code;
    setCard(code);
  }, [history, phase]);

  // 상담사가 목록에서 그 종목을 직접 열었으면 "이동할까요?"는 이미 답이 났다.
  useEffect(() => {
    if (card && card === selectedCode) setCard(null);
  }, [card, selectedCode]);

  return {
    ver,
    setVer,
    phase,
    error,
    elapsed: now - startedAt,
    history,
    card,
    start,
    stop,
    room,
    /** 휴대폰 방 연결·해제 — 해제하면 진행 중 통화도 끝낸다 */
    link: (id: string | null) => {
      if (!id && phaseRef.current === 'live') endCall();
      setRoom(id);
    },
    /** [이동] — 탭을 닫고 그 종목 코드를 돌려준다(목록 선택은 화면이 한다). */
    go: () => {
      const code = card;
      setCard(null);
      return code;
    },
    dismiss: () => {
      setCard(null);
      setDismissed(true);
    },
  };
}
