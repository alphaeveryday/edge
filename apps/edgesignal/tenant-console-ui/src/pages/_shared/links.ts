/** 근거 원문 링크(ALPHA-739) 스킴 화이트리스트 — source_uri 는 벤더 원천 값이라
 * 웹 URL(http/https)만 링크로 연다. 그 외(상대경로·file:·intent: 등)는 링크화하지
 * 않고 텍스트 폴백한다(React 는 javascript: 만 막는다). */
export function isHttpUrl(value: string): boolean {
  return /^https?:\/\//i.test(value);
}
