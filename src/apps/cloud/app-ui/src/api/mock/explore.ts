import type { ThemeDetail, ThemeFeedItem } from '../types';

// 탐색 순위 행의 헤드라인(경제기사 제목형)과 키워드 칩
export const RANK_META: Record<string, { title: string; chips: string[]; ready: boolean }> = {
  AXAI: { title: '메모리 값 3개월째 상승, SK하이닉스 이익률 49%', chips: ['환가 상승 지속', '고부가 비중 확대'], ready: true },
  DEFN: { title: '유럽 무기 주문 3분기 연속 증가, 한화에어로 5년치 일감', chips: ['수주 잔고 확대', '수익성 개선'], ready: true },
  GRID: { title: '美 전기료 인상 승인, 전력회사 배당 2.4%로 상향', chips: ['매출 확정', '배당 매력 확대'], ready: false },
  KBND: { title: '물가 3개월째 2%대, 한국은행 금리 인하 기대 확대', chips: ['인하 여건 성숙', '채권 가격 상승'], ready: false },
  SOLR: { title: '태양광 원료값 2년 만에 바닥, 중국 감산 발표는 아직', chips: ['원료 값 바닥', '감산 대기'], ready: false },
  MEDX: { title: '바이오 임상 3상 발표 연기, 셀트리온 3분기 실적 하향', chips: ['임상 지연', '실적 하향'], ready: false },
};

export const THEME_SHEET: Record<string, { why: string; tags: Record<string, string> }> = {
  'AI·반도체': { why: '메모리 값 3개월째 상승, SK하이닉스 이익률 49%', tags: { AXAI: '재료 확인', GRID: '' } },
  '방산': { why: '유럽 무기 주문 3분기 연속 증가, 한화에어로 5년치 일감', tags: { DEFN: '재료 확인' } },
};

export const THEME_FEED: ThemeFeedItem[] = [
  { key: 'AI·반도체', bg: '#3D34E0', count: 4, headline: 'AI 데이터센터 설비투자가 41%까지 올라왔어요', dir: 'help' },
  { key: '배당·인프라', bg: '#0E8A6C', count: 3, headline: '배당·인프라: 요금 인상은 승인됐고, 이제 금리 인하만 남았어요', dir: 'neutral' },
  { key: '친환경', bg: '#E8A13D', count: 3, headline: '친환경: 원료 값은 바닥인데, 중국이 아직 덜 만들겠다고 안 했어요', dir: 'neutral' },
  { key: '원자재', bg: '#C9820E', count: 2, headline: '원자재: 실질금리가 0.9%까지 내려와 금이 유리해졌어요', dir: 'help' },
  { key: '채권', bg: '#E0562B', count: 1, headline: '채권: 물가가 2.4%로 안정돼 세 번째 인하를 기다려요', dir: 'neutral' },
  { key: '방산', bg: '#131318', count: 1, headline: '방산: 유럽 국방 예산이 19% 늘어 주문이 실적으로 넘어가요', dir: 'help' },
  { key: '바이오', bg: '#8B34E0', count: 1, headline: '바이오: 임상 3상 발표가 미뤄져 실적 전망을 낮췄어요', dir: 'burden' },
];

export const THEME_DETAILS: Record<string, ThemeDetail> = {
  'AI·반도체': {
    key: 'AI·반도체',
    headline: 'AI·반도체: 데이터센터 설비투자가 41%까지 올라왔어요',
    stocks: [
      { name: 'SK하이닉스', logoBg: '#3D34E0', etfs: 'TIGER 반도체TOP10 · KODEX 반도체' },
      { name: '삼성전자', logoBg: '#6C5CF5', etfs: 'TIGER 반도체TOP10 · KODEX 반도체' },
      { name: '한미반도체', logoBg: '#A79BF7', etfs: 'TIGER 반도체TOP10' },
      { name: '리노공업', logoBg: '#CBC4FA', etfs: 'TIGER 반도체TOP10' },
      { name: '엔비디아', logoBg: '#0E8A6C', etfs: 'KODEX 미국AI전력핵심인프라' },
    ],
    intro: 'ETF Orca는 이 테마를 AI 데이터센터 설비투자 증가율 하나로 판단해요. 구간별 비중이 달라도 이 지표가 좋아지는 순서대로 이익 추정이 올라가기 때문이에요.',
    updated: '2026년 8월 30일 업데이트',
    countLabel: 'ETF 4종 비교',
    todayLine: '로봇 주문 증가율 월간 데이터, 전월 대비 개선 · ROBO +3.8%',
    todayEffect: 'AI 데이터센터 설비투자 증가율 흐름은 보합이에요. 전망 판단은 바꾸지 않았어요.',
    importantLead: 'AI 데이터센터 설비투자 증가율 흐름이 이 테마의 방향을 정해요. 현재 41%로 개선세를 이어가고 있어요.',
    importantWhy: '1Q26 22%에서 8월 41%까지 움직였어요. 9월 초 빅테크 설비투자 가이던스에서 이 흐름이 이어지는지 먼저 확인해야 해요.',
    metric: { name: 'AI 데이터센터 설비투자 증가율', now: '41%', dir: 'help', vals: [22, 31, 37, 41], thresh: 35, xLabels: ['1Q26', '2Q26', '7월', '8월(E)'], refLabel: '개선 기준 35%', state: '기준 위' },
    thesis: '설비투자가 늘면 메모리 값이 먼저 오르고, 그다음 장비 주문이 늘어요.',
    surface: '겉으로는 반도체 주가가 AI 기대감으로 오르는 것처럼 보여요.',
    structure: '실제로는 데이터센터가 서버를 더 사면 메모리 값이 오르고, 메모리 회사 이익이 늘고, 그 뒤에 장비 회사 주문이 늘어요.',
    structureWhy: '메모리는 값이 오르면 공장을 더 짓지 않아도 이익이 늘어요. 그래서 설비투자 지표가 메모리 회사 이익보다 한 분기 먼저 움직여요.',
    soWhat: '그래서 이 테마의 ETF는 설비투자 지표가 꺾이기 전까지 전망을 유지해요. 9월 초 가이던스가 첫 확인 지점이에요.',
  },
  '방산': {
    key: '방산',
    headline: '방산: 유럽 국방 예산이 19% 늘어 주문이 실적으로 넘어가요',
    stocks: [
      { name: '한화에어로스페이스', logoBg: '#131318', etfs: 'PLUS K방산' },
      { name: '현대로템', logoBg: '#4E5968', etfs: 'PLUS K방산' },
      { name: 'LIG넥스원', logoBg: '#8B95A1', etfs: 'PLUS K방산' },
      { name: '한국항공우주', logoBg: '#3182F6', etfs: 'PLUS K방산' },
    ],
    intro: 'ETF Orca는 이 테마를 유럽 국방 예산 증가율 하나로 판단해요. 예산이 늘어야 주문이 늘고, 주문이 몇 년에 걸쳐 매출이 되기 때문이에요.',
    updated: '2026년 8월 30일 업데이트',
    countLabel: 'ETF 1종',
    todayLine: '한화에어로 수주 잔고 5년 치 매출 초과 · 기관 사흘 연속 매수',
    todayEffect: '유럽 예산 표결(9월 10일) 전이라 전망 판단은 바꾸지 않았어요.',
    importantLead: '유럽 국방 예산 증가율이 이 테마의 방향을 정해요. 현재 19%로 3년 연속 늘었어요.',
    importantWhy: '2024년 8%에서 2026년 19%까지 올라왔어요. 9월 10일 표결이 통과되면 다음 해 예산이 확정돼요.',
    metric: { name: '유럽 국방 예산 증가율', now: '19%', dir: 'help', vals: [8, 12, 15, 19], thresh: 10, xLabels: ['2023', '2024', '2025', '2026(E)'], refLabel: '기준 10%', state: '기준 위' },
    thesis: '예산이 늘면 주문이 쌓이고, 주문은 5년에 걸쳐 매출이 돼요.',
    surface: '겉으로는 전쟁 뉴스에 방산주가 오르는 것처럼 보여요.',
    structure: '실제로는 유럽 정부가 예산을 통과시키면 한국 회사에 전차·자주포 주문이 들어오고, 그 주문이 5년 동안 매출로 잡혀요.',
    structureWhy: '무기는 주문받고 몇 년에 걸쳐 만들어요. 그래서 주문이 한 번 들어오면 매출이 거의 정해져요.',
    soWhat: '그래서 이 테마는 표결 결과가 나올 때까지 전망을 유지하고, 미뤄지면 낮춰요.',
  },
};
