import type { EtfSummary, Post, WatchGroup } from '../types';

// 디자인 스크립트 ETFS 의 name·theme·logoBg·price·dayChange 값
export const ETFS: EtfSummary[] = [
  { code: 'AXAI', name: 'TIGER 반도체TOP10', theme: 'AI·반도체', logoBg: '#3D34E0', price: 12845, changePct: 3.2, signal: 'strongUp' },
  { code: 'DEFN', name: 'PLUS K방산', theme: '방산', logoBg: '#131318', price: 21480, changePct: 2.1, signal: 'strongUp' },
  { code: 'GRID', name: 'KODEX 미국AI전력핵심인프라', theme: '배당·인프라', logoBg: '#0E8A6C', price: 15320, changePct: 0.6, signal: 'up' },
  { code: 'KBND', name: 'KODEX 국고채30년액티브', theme: '채권', logoBg: '#E0562B', price: 108450, changePct: 0.3, signal: 'neutral' },
  { code: 'MEDX', name: 'TIGER 바이오TOP10', theme: '바이오', logoBg: '#8B34E0', price: 9870, changePct: -1.4, signal: 'down' },
  { code: 'SOLR', name: 'TIGER Fn신재생에너지', theme: '친환경', logoBg: '#E8A13D', price: 6240, changePct: -0.8, signal: 'neutral' },
];

export const GROUPS: WatchGroup[] = [
  { key: 'base', label: '기본 관심' },
  { key: 'ai', label: 'AI 밸류체인' },
];
export const GROUP_MEMBERS: Record<string, string[]> = {
  base: ['AXAI', 'DEFN', 'GRID', 'SOLR'],
  ai: ['AXAI', 'GRID'],
};

export const POSTS: Post[] = [
  {
    id: 'p1', etf: { code: 'AXAI', theme: 'AI·반도체', logoBg: '#3D34E0', short: '반도체TOP10' },
    author: { name: '적립식김씨', handle: '@dca_kim', avatarBg: '#E0562B' }, time: '19분',
    body: '중국 CXMT가 구형 D램 증설을 늘리고 있어요. 이건 아직 숫자로 안 나왔어요.',
    like: 93, reply: 9, repost: 11, liked: false,
  },
  {
    id: 'p2', etf: { code: 'DEFN', theme: '방산', logoBg: '#131318', short: 'K방산' },
    author: { name: '차트만본다', handle: '@chart_only', avatarBg: '#3D34E0' }, time: '34분',
    body: '수주와 이익률은 좋아졌지만, 주가가 먼저 올라 표결 결과에 달렸어요.',
    like: 79, reply: 9, repost: 13, liked: false,
  },
  {
    id: 'p3', etf: { code: 'DEFN', theme: '방산', logoBg: '#131318', short: 'K방산' },
    author: { name: '적립식김씨', handle: '@dca_kim', avatarBg: '#E0562B' }, time: '55분',
    body: '전쟁이 끝나면 발주 속도가 줄어요. 이건 아직 숫자로 안 나왔어요.',
    like: 56, reply: 10, repost: 13, liked: false,
  },
];
