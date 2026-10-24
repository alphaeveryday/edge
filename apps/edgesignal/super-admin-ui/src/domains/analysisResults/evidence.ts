/* analysisResults 도메인 — 전망 근거 항목의 문장·출처를 화면에 그릴 모양으로 고른다 (ALPHA-1337).
 *
 * 저장 형상은 엔진 계약(analysis-engine-v2 `contracts/screen-output.schema.json`)이다:
 * 문장 `{sentence, is_updated}`, 출처 `{title, url}`. 글자는 고쳐 쓰거나 줄이지 않고 그대로 낸다.
 * 예상한 키가 없는 값은 화면이 깨지지 않게 원문으로 보인다 — 그 판단은 `rawText` 한 곳뿐이다. */

export type Bullet = { kind: 'sentence'; text: string; updated: boolean } | { kind: 'raw'; text: string };
export type Source =
  | { kind: 'link'; text: string; href: string }
  | { kind: 'text'; text: string }
  | { kind: 'raw'; text: string };

/** 모양을 모르는 값의 원문 — 글자는 그대로, 나머지는 JSON */
export const rawText = (v: unknown): string => (typeof v === 'string' ? v : (JSON.stringify(v) ?? String(v)));

/** 배열이 아니면 한 칸짜리로 본다. null·없음은 빈 목록이다 */
const asList = (v: unknown): unknown[] => (Array.isArray(v) ? v : v == null ? [] : [v]);

const record = (v: unknown): Record<string, unknown> | null =>
  typeof v === 'object' && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : null;

const nonEmpty = (v: unknown): string | null => (typeof v === 'string' && v !== '' ? v : null);

/** http·https 만 링크로 만든다 — `javascript:` 같은 스킴이 저장돼 있어도 누를 수 있게 되지 않는다 */
export function isWebUrl(url: string): boolean {
  try {
    const { protocol } = new URL(url);
    return protocol === 'http:' || protocol === 'https:';
  } catch {
    return false;
  }
}

export function bullets(v: unknown): Bullet[] {
  return asList(v).map((b): Bullet => {
    const sentence = nonEmpty(record(b)?.sentence);
    if (sentence !== null) return { kind: 'sentence', text: sentence, updated: record(b)?.is_updated === true };
    return { kind: 'raw', text: rawText(b) };
  });
}

export function sources(v: unknown): Source[] {
  return asList(v).map((s): Source => {
    const url = nonEmpty(record(s)?.url);
    if (url === null) return { kind: 'raw', text: rawText(s) };
    const text = nonEmpty(record(s)?.title) ?? url;
    return isWebUrl(url) ? { kind: 'link', text, href: url } : { kind: 'text', text };
  });
}
