import { useEffect, useRef, useState } from 'react';
import { QUEUE, REPORTS } from '../data/reports.ts';
import type { QueuedReport } from '../report.ts';
import { EMPTY, addText, flush, pendingQuestion, setAnswer, startCall, type History } from './history.ts';
import { findEtf, transcribeKeywords } from './keywords.ts';
import { startTranscription, type TranscriptSession } from './realtime.ts';
import { streamSuggestion } from './suggest.ts';

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

export function useCallListener(selectedCode: string | null) {
  const [ver, setVer] = useState<1 | 2>(1);
  const [phase, setPhase] = useState<Phase>('idle');
  const [error, setError] = useState('');
  const [startedAt, setStartedAt] = useState(0);
  const [now, setNow] = useState(Date.now);
  const [history, setHistory] = useState<History>(EMPTY);
  const [card, setCard] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState(false);

  const session = useRef<TranscriptSession | null>(null);
  /** 최근 전사 꼬리 — 비우지 않고 밀어낸다. 조각이 이름 중간에서 끊겨 와도("K방산&우" | "주는") 이어서 읽는다. */
  const recent = useRef('');
  /** 마지막으로 들린 종목 — 같은 종목이 꼬리에 남아 있는 동안 탭을 다시 띄우지 않는다. */
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
    if (!code || code === heard.current) return;
    const s = live.current;
    if (s.dismissed || code === s.selectedCode) return;
    // 이름이 이어 들려 더 긴 이름으로 바뀌면("K방산" → "K방산&우주") 띄운 탭도 바뀐다.
    heard.current = code;
    setCard(code);
  };

  const start = async () => {
    setPhase('connecting');
    setError('');
    setCard(null);
    setDismissed(false);
    recent.current = '';
    heard.current = null;
    setHistory((h) => startCall(h, Date.now()));
    try {
      const s = await startTranscription(KEYWORDS, {
        onDelta: (_id, delta) => {
          setHistory((h) => addText(h, delta, Date.now()));
          recent.current = (recent.current + delta).slice(-RECENT_CHARS);
          clearTimeout(settle.current);
          settle.current = setTimeout(detect, DETECT_SETTLE_MS);
        },
        onCompleted: () => {},
        onError: (message) => setError(message),
      });
      if (!mounted.current) return s.stop();
      session.current = s;
      setStartedAt(Date.now());
      setNow(Date.now());
      setPhase('live');
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
    answering.current?.abort();
    clearTimeout(settle.current);
    setHistory((h) => flush(h, Date.now()));
    setPhase('idle');
    setCard(null);
  };

  // 버전 2: 종목이 정해진 구간에서 새 질문이 끝나면 답변을 만든다. 한 번에 하나 — 끝나면 내역이 바뀌어
  // 이 효과가 다시 돌고, 그사이 쌓인 질문 중 가장 최근 것을 고른다. 실패·빈 답변은 리포트 고객 안내 문구로 대신한다.
  const running = useRef(false);
  const answering = useRef<AbortController | null>(null);
  const historyRef = useRef(history);
  historyRef.current = history;
  useEffect(() => {
    if (ver !== 2 || phase !== 'live' || running.current) return;
    const q = pendingQuestion(history);
    const report = q && etfByCode(q.code);
    if (!q || !report) return;
    running.current = true;
    const id = q.sentence.id;
    setHistory((h) => setAnswer(h, id, { answer: '', answering: true }));
    const ac = new AbortController();
    answering.current = ac;
    const timer = setTimeout(() => ac.abort(), SUGGEST_TIMEOUT_MS);
    streamSuggestion(callText(historyRef.current), report, (t) => setHistory((h) => setAnswer(h, id, { answer: t, answering: true })), ac.signal)
      .then((out) => setHistory((h) => setAnswer(h, id, out.trim() ? { answer: out, answering: false } : { answer: report.say, answering: false, fallback: true })))
      .catch(() => setHistory((h) => setAnswer(h, id, { answer: report.say, answering: false, fallback: true })))
      .finally(() => {
        clearTimeout(timer);
        answering.current = null;
        running.current = false;
      });
  }, [history, ver, phase]);

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
