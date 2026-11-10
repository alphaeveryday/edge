// 상담 답변 스트리밍 — /api/call/suggest 가 DeepSeek 답변 글자를 오는 대로 흘려보낸다.
import type { QueuedReport } from '../report.ts';

/** 답변 생성에 넘길 리포트 요약 — 화면에 보이는 사실만 담는다. */
export function reportContext(r: QueuedReport): string {
  const lines = [`${r.name}(${r.code}) 오늘 ${r.pct > 0 ? '+' : ''}${r.pct.toFixed(2)}%`, `요약: ${r.title}`, `고객 안내: ${r.say}`];
  for (const g of r.groups) lines.push(`${g.head}: ` + g.items.map((it) => `${it.t}(${it.sub})`).join(' / '));
  return lines.join('\n');
}

export async function streamSuggestion(
  transcript: string,
  report: QueuedReport,
  onText: (soFar: string) => void,
  signal: AbortSignal,
): Promise<string> {
  const res = await fetch('/api/call/suggest', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ transcript, report: reportContext(report) }),
    signal,
  });
  if (!res.ok || !res.body) throw new Error(`답변 생성 실패(${res.status})`);
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let text = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) return text;
    text += value;
    onText(text);
  }
}
