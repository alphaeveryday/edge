import type { Candle, ChartData, EtfDetailData, MoveInfo, Post } from '../types';

// 결정적 의사난수 (시드 = 종목 코드)
const hash = (s: string) => {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = (h * 16777619) >>> 0; }
  return h;
};
const rnd = (seed: number) => { let x = seed || 1; return () => { x ^= x << 13; x ^= x >>> 17; x ^= x << 5; return ((x >>> 0) % 10000) / 10000; }; };

export function chartOf(code: string, price: number, changePct: number, range = '1D'): ChartData {
  const r = rnd(hash(code + range));
  const n = 22;
  const end = price;
  const start = end / (1 + changePct / 100) / (1 + (r() * 0.16 - 0.04));
  const candles: Candle[] = [];
  let c = start;
  for (let i = 0; i < n; i++) {
    const target = start + ((end - start) * (i + 1)) / n;
    const o = c;
    const drift = (target - o) * (0.4 + r() * 0.8) + (r() - 0.5) * price * 0.012;
    c = i === n - 1 ? end : o + drift;
    const h = Math.max(o, c) + r() * price * 0.008;
    const l = Math.min(o, c) - r() * price * 0.008;
    candles.push({ o: Math.round(o), h: Math.round(h), l: Math.round(l), c: Math.round(c) });
  }
  const ma = (k: number) => candles.map((_, i) => (i + 1 < k ? null : Math.round(candles.slice(i + 1 - k, i + 1).reduce((a, x) => a + x.c, 0) / k)));
  return { range, candles, ma5: ma(5), ma20: ma(8), axis: ['08.05', '08.13', '08.21', '08.28', '09.04'] };
}

export const MOVES: Record<string, MoveInfo> = {
  DEFN: {
    ago: '오늘 08:30',
    text: '전차·자주포를 만드는 한화에어로스페이스가 5년 치 주문을 받았어요. 이 ETF의 20%예요.',
    foot: '관련 이슈 4개',
    sheetTitle: '수주·수익성은 좋지만 밸류 부담이 남아 있습니다.',
    groups: [
      { head: '오늘 오른 이유', items: [
        { dir: 'help', t: '유럽 무기 주문 3분기 연속 증가', sub: '전차·자주포를 만드는 한화에어로스페이스가 5년 치 주문을 받았어요. 이 ETF의 20%예요. 폴란드·루마니아 국방 예산에 추가 주문이 들어 있어요.' },
        { dir: 'help', t: '한화에어로 이익률 11.4%', sub: '공장을 더 돌리면 무기 하나당 비용이 줄어요. 그래서 남는 돈이 늘어요. 유로 환율(1,460원)도 이익을 1%p 높여줘요.' },
        { dir: 'help', t: '기관 사흘 연속 매수·최고가', sub: '국내 기관이 방산주를 사며 이 ETF가 역대 최고가를 썼어요.' },
      ] },
      { head: '걸리는 것', items: [
        { dir: 'burden', t: '유럽 예산 표결 9월 10일 대기', sub: '한화에어로스페이스 주가가 1년 이익의 31배예요. 표결이 통과돼야 이 주가가 말이 돼요. 미뤄지면 빠져요.' },
      ] },
    ],
  },
  AXAI: {
    ago: '오늘 08:30',
    text: '서버용 D램 값이 3개월 동안 18% 올랐어요. 메모리를 만드는 SK하이닉스·삼성전자가 이 ETF의 36%예요.',
    foot: '관련 이슈 3개',
    sheetTitle: '수요는 확인됐고, 남은 상승 폭이 관건이에요.',
    groups: [
      { head: '오늘 오른 이유', items: [
        { dir: 'help', t: '서버용 D램 값 3개월 연속 상승', sub: 'AI 서버를 만드는 회사들이 메모리를 더 주문했어요.' },
        { dir: 'help', t: 'SK하이닉스 이익률 42% → 49%', sub: '메모리는 값이 오르면 이익이 그대로 늘어요.' },
      ] },
      { head: '걸리는 것', items: [
        { dir: 'burden', t: '주가 20일 +18% 선반영', sub: '9월 12일 양산 수율이 80%를 넘어야 남은 폭이 열려요.' },
      ] },
    ],
  },
};
const genericMove = (name: string): MoveInfo => ({
  ago: '오늘 08:30', text: `${name}은 오늘 새로 확인된 사건이 없어요. 어제 흐름이 이어지고 있어요.`, foot: '관련 이슈 0개',
  sheetTitle: '오늘 새로 확인된 것이 없어요',
  groups: [{ head: '지켜볼 것', items: [{ dir: 'neutral', t: '전망을 바꿀 사건이 생기면 알려드려요' }] }],
});
export const moveOf = (code: string, name: string) => MOVES[code] ?? genericMove(name);

export const DETAILS: Record<string, EtfDetailData> = {
  AXAI: {
    insight: { dir: 'help', text: '메모리 2사가 36%예요. 서버용 D램 값이 오르면 이 ETF의 3분의 1이 같이 좋아져요.' },
    stocks: [
      { name: 'SK하이닉스', weight: 22, changePct: 3.4, dir: 'help' },
      { name: '삼성전자', weight: 14, changePct: 2.8, dir: 'help' },
      { name: '한미반도체', weight: 9, changePct: 1.2, dir: 'neutral' },
      { name: '리노공업', weight: 7, changePct: -0.4, dir: 'neutral' },
      { name: '기타 28종', weight: 48, changePct: 0.5, dir: 'neutral' },
    ],
    themes: [
      { name: '반도체', weight: 42, changePct: 3.1, dir: 'help' },
      { name: '전력 인프라', weight: 27, changePct: 0.9, dir: 'neutral' },
      { name: '클라우드', weight: 21, changePct: 1.4, dir: 'help' },
      { name: '기타', weight: 10, changePct: 0.2, dir: 'neutral' },
    ],
    holdings: [
      { name: 'SK하이닉스', weight: 22, dir: 'help', desc: 'HBM4를 고객 3곳에 처음 출하했어요. 영업이익률이 49%예요.' },
      { name: '삼성전자', weight: 14, dir: 'help', desc: '서버용 D램 값 상승의 두 번째 수혜예요.' },
      { name: '한미반도체', weight: 9, dir: 'neutral', desc: 'HBM 장비를 만들어요. 양산 수율이 확인돼야 주문이 늘어요.' },
      { name: '리노공업', weight: 7, dir: 'neutral', desc: '검사용 부품을 만들어요. 오늘 재료와는 거리가 있어요.' },
    ],
    themeRows: [
      { name: '반도체', weight: 42, dir: 'help', desc: '오늘 재료가 실리는 쪽이에요.' },
      { name: '전력 인프라', weight: 27, dir: 'neutral', desc: '데이터센터 투자가 퍼져야 같이 좋아져요.' },
      { name: '클라우드', weight: 21, dir: 'help', desc: 'AI 서비스 매출이 늘고 있어요.' },
    ],
    stockCount: 32,
    info: [{ k: '총보수', v: '0.45%' }, { k: '분배율', v: '1.8%' }, { k: '추적오차', v: '0.12%' }, { k: '운용사', v: '미래에셋' }],
    blurb: '반도체·전력·클라우드까지 AI 인프라 밸류체인을 한 번에 담아요. 수요는 실측 국면, 이제 남은 상승 폭이 관건이에요.',
  },
  DEFN: {
    insight: { dir: 'help', text: '전차·자주포 회사가 38%예요. 유럽 주문이 늘면 이 ETF의 3분의 1 이상이 같이 좋아져요.' },
    stocks: [
      { name: '한화에어로스페이스', weight: 20, changePct: 3.1, dir: 'help' },
      { name: '현대로템', weight: 18, changePct: 2.4, dir: 'help' },
      { name: 'LIG넥스원', weight: 12, changePct: 1.1, dir: 'neutral' },
      { name: '한국항공우주', weight: 11, changePct: 0.6, dir: 'neutral' },
      { name: '기타 6종', weight: 39, changePct: 1.2, dir: 'neutral' },
    ],
    themes: [
      { name: '지상 무기', weight: 38, changePct: 2.8, dir: 'help' },
      { name: '항공·우주', weight: 31, changePct: 0.9, dir: 'neutral' },
      { name: '유도 무기', weight: 21, changePct: 1.1, dir: 'neutral' },
      { name: '기타', weight: 10, changePct: 0.4, dir: 'neutral' },
    ],
    holdings: [
      { name: '한화에어로스페이스', weight: 20, dir: 'help', desc: '5년 치 주문을 받았어요. 이익률 11.4%예요.' },
      { name: '현대로템', weight: 18, dir: 'help', desc: '폴란드 전차 2차 계약을 기다리고 있어요.' },
      { name: 'LIG넥스원', weight: 12, dir: 'neutral', desc: '유도 무기 수출은 이번 재료와 거리가 있어요.' },
    ],
    themeRows: [
      { name: '지상 무기', weight: 38, dir: 'help', desc: '유럽 주문이 실리는 쪽이에요.' },
      { name: '항공·우주', weight: 31, dir: 'neutral', desc: '오늘 재료와 무관해요.' },
    ],
    stockCount: 10,
    info: [{ k: '총보수', v: '0.45%' }, { k: '분배율', v: '0.9%' }, { k: '추적오차', v: '0.15%' }, { k: '운용사', v: '한화' }],
    blurb: '국내 방산 대표 10종을 담아요. 유럽 국방 예산이 늘어나는 동안 주문이 쌓이는 구조예요.',
  },
};
const genericDetail = (name: string): EtfDetailData => ({
  insight: { dir: 'neutral', text: `${name}의 구성은 어제와 같아요.` },
  stocks: [{ name: '상위 10종', weight: 60, changePct: 0.3, dir: 'neutral' }, { name: '기타', weight: 40, changePct: 0.1, dir: 'neutral' }],
  themes: [{ name: '주요 테마', weight: 70, changePct: 0.2, dir: 'neutral' }, { name: '기타', weight: 30, changePct: 0.1, dir: 'neutral' }],
  holdings: [], themeRows: [], stockCount: 0,
  info: [{ k: '총보수', v: '-' }, { k: '분배율', v: '-' }],
  blurb: '',
});
export const detailOf = (code: string, name: string) => DETAILS[code] ?? genericDetail(name);

export const ETF_POSTS: Post[] = [
  { id: 'd1', etf: { code: 'DEFN', theme: '방산', logoBg: '#131318', short: 'K방산' }, author: { name: '차트만본다', handle: '@chart_only', avatarBg: '#3D34E0' }, time: '34분', quoteTag: 'AI 분석 · 오늘',
    body: '수주와 이익률은 좋아졌지만, 주가가 먼저 올라 표결 결과에 달렸어요.', like: 79, reply: 9, repost: 13, liked: false },
  { id: 'd2', etf: { code: 'DEFN', theme: '방산', logoBg: '#131318', short: 'K방산' }, author: { name: '적립식김씨', handle: '@dca_kim', avatarBg: '#E0562B' }, time: '18분',
    title: '전쟁이 끝나면 발주 속도가 줄어요.', body: '이건 아직 숫자로 안 나왔어요.', like: 56, reply: 10, repost: 13, liked: false },
  { id: 'd3', etf: { code: 'DEFN', theme: '방산', logoBg: '#131318', short: 'K방산' }, author: { name: '적립식김씨', handle: '@dca_kim', avatarBg: '#E0562B' }, time: '55분',
    title: '수주 잔고가 분배금에도 반영될까요?', body: 'K방산 3년째 들고 있어요.', like: 43, reply: 10, repost: 1, liked: false },
  { id: 'a1', etf: { code: 'AXAI', theme: 'AI·반도체', logoBg: '#3D34E0', short: '반도체TOP10' }, author: { name: '적립식김씨', handle: '@dca_kim', avatarBg: '#E0562B' }, time: '19분',
    body: '중국 CXMT가 구형 D램 증설을 늘리고 있어요. 이건 아직 숫자로 안 나왔어요.', like: 93, reply: 9, repost: 11, liked: false },
  { id: 'a2', etf: { code: 'AXAI', theme: 'AI·반도체', logoBg: '#3D34E0', short: '반도체TOP10' }, author: { name: '분할매수중', handle: '@split_buy', avatarBg: '#0E8A6C' }, time: '32분',
    body: '반도체TOP10 저도 같은 생각이에요. 다만 강력 상승 판단이 유지되는지 한 주 더 보려고요.', like: 12, reply: 3, repost: 0, liked: false,
    repostOf: { name: '적립식김씨', avatarBg: '#E0562B', time: '19분', body: '중국 CXMT가 구형 D램 증설을 늘리고 있어요. 이건 아직 숫자로 안 나왔어요.' } },
];
