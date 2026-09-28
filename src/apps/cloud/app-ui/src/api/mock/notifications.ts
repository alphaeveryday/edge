import type { Notification } from '../types';

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
