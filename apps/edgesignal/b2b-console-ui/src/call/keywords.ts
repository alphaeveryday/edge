// 통화 전사에서 데모 ETF 를 찾는 순수 로직 — node --test 로 직접 검사한다(keywords.test.ts).
// 전사는 "코덱스 로봇 액티브", "KODEX로봇" 처럼 띄어쓰기·영문 표기가 섞여 온다. 둘 다 같은 꼴로 맞춘 뒤 별칭을 찾는다.

/** 종목 코드 → 정규화된 별칭. 짧은 별칭은 다른 종목과 겹치지 않는 것만 둔다(예: "반도체" 단독은 없음). */
export const ALIASES: Record<string, string[]> = {
  '445290': ['로봇액티브', 'kodex로봇', '로봇'],
  '475310': ['반도체후공정', '후공정'],
  '377990': ['신재생에너지', '신재생', '태양광'],
  '490480': ['solk방산', 'k방산', '방산'],
  '463250': ['k방산우주', '방산우주', '우주'],
  '471990': ['ai반도체핵심장비', '반도체핵심장비', '핵심장비', '반도체장비'],
  '476260': ['반도체핵심공정주도주', '핵심공정', '공정주도주'],
  '261070': ['코스닥150바이오테크', '바이오테크', '바이오'],
  '0210A0': ['k반도체top2', '반도체top2', 'top2'],
  '102960': ['kodex기계장비', '기계장비'],
};

// 한글로 받아 적힌 영문 표기를 영문으로 돌린다. 긴 것부터 바꿔야 "에이아이"가 "에이스"보다 먼저 잡히지 않는 일이 없다.
const READINGS: [RegExp, string][] = [
  [/코덱스/g, 'kodex'],
  [/타이거/g, 'tiger'],
  [/하나로/g, 'hanaro'],
  [/에이아이/g, 'ai'],
  [/에이스/g, 'ace'],
  [/에프엔/g, 'fn'],
  [/탑투|탑2|톱투|톱2/g, 'top2'],
  [/케이(?=방산|반도체)/g, 'k'],
  [/솔(?=k방산)/g, 'sol'],
  [/앤드|앤|and/g, ''],
];

export function normalize(text: string): string {
  let s = text.toLowerCase().replace(/[^0-9a-z가-힣]/g, '');
  for (const [re, to] of READINGS) s = s.replace(re, to);
  return s;
}

/**
 * 가장 늦게 끝나는 별칭의 종목을 고른다. 끝 위치가 같으면 긴 별칭이 이긴다 —
 * "K방산&우주"는 "k방산"(SOL K방산)보다 "k방산우주"(TIGER K방산&우주)로 읽어야 한다.
 */
export function findEtf(text: string): string | null {
  const s = normalize(text);
  let best: { code: string; end: number; len: number } | null = null;
  for (const [code, aliases] of Object.entries(ALIASES)) {
    for (const a of aliases) {
      const at = s.lastIndexOf(a);
      if (at < 0) continue;
      const end = at + a.length;
      if (!best || end > best.end || (end === best.end && a.length > best.len)) best = { code, end, len: a.length };
    }
  }
  return best?.code ?? null;
}

/** 전사 모델에 미리 알려 줄 용어 — 정식 이름과 소리 나는 대로 쓴 이름. 서버 한도(80개·40자) 안. */
export function transcribeKeywords(names: string[]): string[] {
  const spoken = ['코덱스', '타이거', '솔', '하나로', '에이스', '케이방산', '후공정', '신재생에너지', '바이오테크', '핵심장비', '핵심공정'];
  return [...new Set([...names, ...spoken])].filter((k) => k.length <= 40).slice(0, 80);
}
