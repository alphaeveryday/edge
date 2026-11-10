// 통화 내역 — 전사 조각을 "통화 → 종목 구간 → 문장" 단위로 쌓는 순수 로직(history.test.ts).
// 전사는 문장부호가 붙어 온다("…올랐어요? 지금…"). 문장이 끝나야 내역에 올리고, 끝나지 않은 꼬리는 partial 로 둔다.
import { findEtf } from './keywords.ts';

export interface Sentence {
  id: number;
  text: string;
  /** 문장이 끝난 시각(epoch ms) */
  at: number;
  question: boolean;
  /** 버전 2: 이 질문에 추천한 답변(만드는 중이면 이어 붙는다) */
  answer?: string;
  answering?: boolean;
  /** 답변 생성이 실패해 리포트 고객 안내 문구로 대신했다 */
  fallback?: boolean;
}

/** 같은 통화 안에서 같은 종목을 이야기한 구간. code 가 null 이면 아직 종목이 안 나온 구간. */
export interface Segment {
  call: number;
  code: string | null;
  at: number;
  sentences: Sentence[];
}

export interface History {
  segments: Segment[];
  partial: string;
  nextId: number;
  calls: number;
}

export const EMPTY: History = { segments: [], partial: '', nextId: 1, calls: 0 };

/** 문장부호 없이 이만큼 길어지면 띄어쓰기에서 끊는다 — 내역이 한 줄로 끝없이 늘지 않게. */
const MAX_PARTIAL = 120;

export function startCall(h: History, at: number): History {
  const call = h.calls + 1;
  return { ...h, calls: call, partial: '', segments: [...h.segments, { call, code: null, at, sentences: [] }] };
}

const END = /[.?!。？！]/;
const isDigit = (c: string | undefined) => c !== undefined && c >= '0' && c <= '9';

/**
 * 끝난 문장들과 남은 꼬리로 나눈다. 숫자 사이의 점("1.5%")은 문장 끝이 아니다 — 조각이 "1." 에서 끊겨 와도
 * 다음 글자를 볼 때까지 끝으로 치지 않는다.
 */
export function splitSentences(text: string): { done: string[]; rest: string } {
  const done: string[] = [];
  let last = 0;
  for (let i = 0; i < text.length; i++) {
    if (!END.test(text[i])) continue;
    if (text[i] === '.' && isDigit(text[i - 1]) && (i + 1 === text.length || isDigit(text[i + 1]))) continue;
    let j = i + 1;
    while (j < text.length && END.test(text[j])) j++;
    const s = text.slice(last, j).trim();
    if (s.replace(/[.?!。？！]/g, '').trim()) done.push(s);
    last = j;
    i = j - 1;
  }
  let rest = text.slice(last);
  if (rest.length > MAX_PARTIAL) {
    const cut = rest.lastIndexOf(' ', MAX_PARTIAL);
    const at = cut > 0 ? cut : MAX_PARTIAL;
    done.push(rest.slice(0, at).trim());
    rest = rest.slice(at);
  }
  return { done, rest: rest.trimStart() };
}

export function addText(h: History, delta: string, at: number): History {
  if (!h.segments.length) h = startCall(h, at);
  const { done, rest } = splitSentences(h.partial + delta);
  return push(h, done, rest, at);
}

/** 통화가 끝날 때 문장부호 없이 남은 마지막 말도 내역에 올린다. */
export function flush(h: History, at: number): History {
  const tail = h.partial.trim();
  return tail && h.segments.length ? push(h, [tail], '', at) : h;
}

function push(h: History, done: string[], rest: string, at: number): History {
  if (!done.length) return { ...h, partial: rest };
  const segments = h.segments.slice();
  let nextId = h.nextId;
  for (const text of done) {
    const cur = segments[segments.length - 1];
    const code = findEtf(text);
    const sentence: Sentence = { id: nextId++, text, at, question: /[?？]$/.test(text) };
    // 다른 종목 이름이 나온 문장부터 새 구간. 종목이 없던 첫 구간에서 처음 나오면 그 구간에 이름만 붙인다.
    if (code && code !== cur.code) {
      if (cur.code === null && cur.sentences.length === 0) segments[segments.length - 1] = { ...cur, code, sentences: [sentence] };
      else segments.push({ call: cur.call, code, at, sentences: [sentence] });
    } else {
      segments[segments.length - 1] = { ...cur, sentences: [...cur.sentences, sentence] };
    }
  }
  return { ...h, segments, partial: rest, nextId };
}

/** 지금 이야기 중인 종목(마지막 구간의 종목). */
export function currentCode(h: History): string | null {
  return h.segments[h.segments.length - 1]?.code ?? null;
}

// 맞장구·인사처럼 답할 거리가 없는 짧은 말 — 답변 단위에서 뺀다.
const BACKCHANNEL = /^(네+|넵|예|응|음+|어+|아+|아아|그렇군요|그렇구나|그래요|알겠습니다|알겠어요|감사합니다|고맙습니다|여보세요|좋아요|맞아요|네네)[.?!~,\s]*$/;

/** 답할 거리가 있는 말인지 — 문장부호·띄어쓰기를 뺀 6글자 이상이고 맞장구가 아니다. */
export function meaningful(text: string): boolean {
  return text.replace(/[\s.?!,~…]/g, '').length >= 6 && !BACKCHANNEL.test(text.trim());
}

/**
 * 답변 단위(차례) — 지금 통화에서 마지막으로 답한 말 뒤에 쌓인, 답할 거리가 있는 말들.
 * 질문이 끝나면 바로, 아니면 말이 잠깐 멈췄을 때(화면이 판단) 이 차례 전체에 한 번 답한다.
 * 종목은 그 차례 마지막 말이 속한 구간의 종목(없으면 null — 시장 전체 요약으로 답한다).
 */
export function pendingTurn(h: History): { sentence: Sentence; code: string | null; asked: boolean; text: string } | null {
  const said = h.segments.filter((seg) => seg.call === h.calls).flatMap((seg) => seg.sentences.map((s) => ({ s, code: seg.code })));
  let from = 0;
  for (let i = said.length - 1; i >= 0; i--) {
    if (said[i].s.answer !== undefined) {
      from = i + 1;
      break;
    }
  }
  const turn = said.slice(from).filter((x) => meaningful(x.s.text));
  if (!turn.length) return null;
  const last = turn[turn.length - 1];
  return { sentence: last.s, code: last.code, asked: turn.some((x) => x.s.question), text: turn.map((x) => x.s.text).join(' ') };
}

export function setAnswer(h: History, id: number, patch: Pick<Sentence, 'answer' | 'answering' | 'fallback'>): History {
  return {
    ...h,
    segments: h.segments.map((seg) =>
      seg.sentences.some((s) => s.id === id) ? { ...seg, sentences: seg.sentences.map((s) => (s.id === id ? { ...s, ...patch } : s)) } : seg,
    ),
  };
}

/** 지금 통화에서 그 종목에 대해 가장 최근에 추천한(또는 만드는 중인) 답변 — 지난 고객의 답변은 보이지 않는다. */
export function latestAnswer(h: History, code: string): Sentence | null {
  for (let i = h.segments.length - 1; i >= 0 && h.segments[i].call === h.calls; i--) {
    const seg = h.segments[i];
    if (seg.code !== code) continue;
    const s = [...seg.sentences].reverse().find((x) => x.answer !== undefined);
    if (s) return s;
  }
  return null;
}
