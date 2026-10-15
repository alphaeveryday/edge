// 서버 검색과 같은 대소문자·공백 무시 부분 일치
const fold = (s: string) => s.toLowerCase().replace(/\s+/g, '');

export const matches = (q: string, ...texts: (string | undefined)[]) => {
  const k = fold(q);
  return !k || texts.some((t) => !!t && fold(t).includes(k));
};
