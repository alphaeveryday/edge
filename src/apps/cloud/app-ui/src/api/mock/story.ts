import type { Notification, StoryCard } from '../types';

export const STORY_CARDS: Record<string, { sub: string; cards: StoryCard[] }> = {
  AXAI: {
    sub: '데일리 · 9/5 금',
    cards: [
      {
        kind: 'ai', sec: '오늘 바뀐 것', badge: '가격 변동', changePct: 3.2,
        headline: 'HBM 샘플 공급 확인, 단기 변동성 주의',
        noteTitle: '현재 관측된 반응',
        notes: ['AI 서버용 메모리 값이 3개월째 올라요.', 'SK하이닉스가 차세대 AI 메모리(HBM4)를 고객 3곳에 처음 보냈어요.', '외국인이 이 ETF를 5일 중 4일 샀어요.'],
        news: [
          { issueId: 'i3', t: 'SK하이닉스, HBM4 샘플 고객사 3곳 공급 개시', phase: '핵심 관문', kw: '4분기 양산 · 수율 80% 확인 전', dir: 'help' },
          { issueId: 'i1', t: '중국 CXMT, HBM 양산 라인 증설 발표', phase: '체약 요인', kw: '증가시 물량 확대 · 단기 경합 부담', dir: 'burden' },
        ],
      },
      { kind: 'news', sec: '관련 이슈', issueId: 'i3', t: 'SK하이닉스가 HBM4 샘플을 고객사 3곳에 공급하기 시작했어요', kw: '4분기 양산 관문 · 수율 80% 확인 전', b: '샘플 공급은 시작일 뿐이에요. 인증을 통과해 양산 물량이 잡혀야 이익으로 연결돼요. 시장의 시선은 9월 12일 공개될 양산 수율로 옮겨가 있어요.' },
      { kind: 'hook', sec: '그래서', big: '수요는 확인됐어요.\n남은 폭이 관건이에요.', capPre: '주가가 20일 동안 18% 먼저 올라, ', capB: '9월 12일 수율이 80%를 넘는지', capPost: '에 달렸어요.' },
    ],
  },
  DEFN: {
    sub: '데일리 · 9/5 금',
    cards: [
      {
        kind: 'ai', sec: '오늘 바뀐 것', badge: '가격 변동', changePct: 2.1,
        headline: '유럽 무기 주문 3분기 연속 증가, 표결이 남았어요',
        noteTitle: '현재 관측된 반응',
        notes: ['한화에어로 수주 잔고가 5년 치 매출을 넘었어요.', '기관이 3일 연속 사서 ETF는 최고가를 다시 썼어요.', '외국계 증권사 한 곳이 한화에어로 목표주가를 내렸어요.'],
        news: [{ issueId: 'i6', t: '폴란드·루마니아 국방예산 9월 10일 표결', phase: '핵심 관문', kw: '통과 시 K2·천무 추가 주문 공식화', dir: 'help' }],
      },
      { kind: 'hook', sec: '그래서', big: '수주는 5년 치.\n표결 결과에 달렸어요.', capPre: '주가가 먼저 올라 ', capB: '9월 10일 표결', capPost: '이 통과돼야 지금 주가가 말이 돼요.' },
    ],
  },
};

export const NOTIFICATIONS: Notification[] = [
  { id: 'n1', kind: 'comm', etf: 'AXAI', postId: 'p1', time: '11:42 오늘', title: '내 글에 댓글 3개가 달렸어요', body: '"수율 80% 전에 사는 건 이르다"에 답글이 이어지고 있어요.', read: false },
  { id: 'n2', kind: 'watch', etf: 'AXAI', time: '08:32 오늘', title: '3분기 배당 상향 확정', body: '[미국AI전력핵심인프라] 배당 비중 18% · FOMC 금리 결정 (9월 17일)', read: false },
  { id: 'n3', kind: 'watch', etf: 'DEFN', time: '08:31 오늘', title: '수주 잔고, 5년 치 매출 초과', body: '[K방산] 한화에어로스페이스 비중 20% · 유럽 주문국 예산안 표결 (9월 10일)', read: false },
  { id: 'n4', kind: 'watch', etf: 'AXAI', time: '08:30 오늘', title: '메모리 고정가 3개월 연속 상승', body: '[반도체TOP10] SK하이닉스 비중 22% · SK하이닉스 4분기 양산 수율 (9월 12일 공개)', read: false },
  { id: 'n5', kind: 'watch', etf: 'AXAI', time: '07:00 오늘', title: '반도체TOP10 데일리 분석이 올라왔어요', body: 'HBM 수요 확인보다 남은 상승 여력의 크기가 중요해졌다', read: true },
  { id: 'n6', kind: 'watch', etf: 'DEFN', time: '07:00 오늘', title: 'K방산 데일리 분석이 올라왔어요', body: '수주 잔고보다 양산 마진의 개선 속도가 중요해졌다', read: true },
  { id: 'n7', kind: 'content', time: '07:00 오늘', title: '오늘의 시황', body: '반도체 강세에 코스피 상승 출발, 금리 결정은 9월 17일이에요.', read: true },
  { id: 'n8', kind: 'comm', etf: 'AXAI', time: '18:20 어제', title: '오늘의 투표 결과가 나왔어요', body: '반도체TOP10 · 산다 선택이 더 많았어요.', read: true },
  { id: 'n9', kind: 'watch', etf: 'AXAI', time: '16:00 어제', title: '고객 3사 샘플 공급 시작', body: '[반도체TOP10] 어제 +3.2% 마감', read: true },
];
