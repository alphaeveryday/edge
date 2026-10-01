import type { Axis, DailyAnalysis, FactorPage, Hint, MetricPage } from '../types';

const DATES = [
  { key: '2026-08-30', w: '토', d: '30', hasDaily: false },
  { key: '2026-08-31', w: '일', d: '31', hasDaily: false },
  { key: '2026-09-01', w: '월', d: '1', hasDaily: true },
  { key: '2026-09-02', w: '화', d: '2', hasDaily: true },
  { key: '2026-09-03', w: '수', d: '3', hasDaily: true },
  { key: '2026-09-04', w: '목', d: '4', hasDaily: true },
  { key: '2026-09-05', w: '금', d: '5', hasDaily: true },
];
const DATELINE = 'ETF Orca AI · 9월 5일 금요일 08:30';

export const DAILY: Record<string, DailyAnalysis> = {
  AXAI: {
    date: '2026-09-05', dates: DATES, headTitle: '오늘 발행',
    question: '메모리 값 3개월째 상승, SK하이닉스 이익률 49%', dateline: DATELINE,
    now: 'strongUp', prev: 'up',
    synth: 'AI 서버용 메모리 수요는 확인됐어요. 이제 남은 상승 폭이 관건이에요.',
    axes: [
      { axis: '이슈', dir: 'help', summary: '서버용 D램 값이 3개월 동안 18% 올랐어요.', hasPage: true },
      { axis: '차트', dir: 'help', summary: '최근 20일 +18%로 주가가 먼저 따라왔어요.', hasPage: true },
      { axis: '매크로', dir: 'neutral', summary: '원/달러 1,377원은 수출 이익률에 중립이에요.', hasPage: true },
      { axis: '밸류', dir: 'burden', summary: 'SK하이닉스 주가가 1년 이익의 12배라 평소보다 비싸요.', hasPage: true },
      { axis: '수급', dir: 'help', summary: '외국인이 최근 5일 중 4일 대형주를 샀어요.', hasPage: true },
    ],
    title: 'SK하이닉스가 HBM4를 고객 3곳에 처음 출하했어요. 다음은 9월 12일 양산 수율 공개예요',
    todayDate: '9월 5일',
    today: ['SK하이닉스 영업이익률은 42%에서 49%가 됐어요.', 'SK하이닉스가 HBM4를 고객 3곳에 처음 출하했어요.', '외국인이 최근 5일 중 4일 대형주를 샀어요.'],
    args: [
      { no: '01', claim: '서버용 D램 값이 3개월 동안 18% 올랐어요', body: ['AI 서버를 만드는 회사들이 메모리를 더 많이 주문했어요.', '메모리를 만드는 SK하이닉스·삼성전자가 이 ETF의 36%예요.'] },
      { no: '02', claim: 'SK하이닉스 이익률이 42%에서 49%가 됐어요', body: ['메모리는 값이 오르면 이익이 그대로 늘어요.', '공장을 더 짓지 않아도 같은 칩을 더 비싸게 팔기 때문이에요.'] },
      { no: '03', claim: '그런데 주가가 20일 동안 18% 먼저 올랐어요', body: ['시장도 같은 걸 봤고 이미 가격에 반영했어요.', '그래서 9월 12일 양산 수율이 80%를 넘어야 남은 폭이 열려요.'] },
    ],
    closingTitle: '지금 사도 될까요?',
    neg: ['주가가 먼저 올랐어요', '양산 수율 공개 전'],
    pos: ['메모리 값 3개월 상승', '이익률 49%', '외국인 매수'],
    close: '수요는 확인됐지만 주가가 먼저 올라, 9월 12일 수율이 80%를 넘는지에 달렸어요.',
  },
  DEFN: {
    date: '2026-09-05', dates: DATES, headTitle: '오늘 발행',
    question: '유럽 무기 주문 3분기 연속 증가, 한화에어로 5년치 일감', dateline: DATELINE,
    now: 'strongUp',
    synth: '수주와 이익률은 좋아졌지만, 주가가 먼저 올라 표결 결과에 달렸어요.',
    axes: [
      { axis: '이슈', dir: 'help', summary: '유럽이 3분기 연속 무기 주문을 늘렸어요.', hasPage: true },
      { axis: '차트', dir: 'help', summary: '기관이 3일 연속 사서 최고가를 다시 썼어요.', hasPage: true },
      { axis: '매크로', dir: 'help', summary: '유로 환율 1,460원이 이익을 1%p 높여줘요.', hasPage: true },
      { axis: '밸류', dir: 'burden', summary: '한화에어로 주가가 1년 이익의 31배라 평소보다 40% 비싸요.', hasPage: true },
      { axis: '수급', dir: 'help', summary: '국내 기관이 방산주를 사흘 연속 샀어요.', hasPage: true },
    ],
    title: '한화에어로스페이스가 5년 치 주문을 받았어요. 다음은 9월 10일 표결이에요',
    todayDate: '9월 5일',
    today: ['한화에어로 수주 잔고가 5년 치 매출을 넘었어요.', '기관이 3일 연속 사서 ETF는 최고가를 다시 썼어요.', '외국계 증권사 한 곳이 한화에어로 목표주가를 내렸어요.'],
    args: [
      { no: '01', claim: '유럽이 3분기 연속 무기 주문을 늘렸어요', body: ['폴란드·루마니아가 전차와 자주포를 추가로 주문했어요.', '전차·자주포를 만드는 회사가 이 ETF의 38%예요.'] },
      { no: '02', claim: '한화에어로 일감이 5년 치 쌓였어요', body: ['한화에어로 수주 잔고가 5년 치 매출을 넘었어요.', '무기는 주문받고 몇 년에 걸쳐 만들어요. 그래서 5년 동안 매출이 거의 정해진 거예요.', '2022년 주문이 몰렸을 때도 잔고가 3년 연속 늘었어요.'] },
      { no: '03', claim: '그런데 주가가 먼저 올라 표결에 달렸어요', body: ['유럽 국방 예산 표결이 9월 10일이에요.', '통과돼야 지금 주가가 말이 되고, 미뤄지면 빠져요.'] },
    ],
    closingTitle: '지금 사도 될까요?',
    neg: ['주가가 먼저 올랐어요', '유럽 예산 표결 전'],
    pos: ['수주 5년 치', '이익률 11.4%', '기관 사흘 매수'],
    close: '수주와 이익률은 좋아졌지만, 주가가 먼저 올라 9월 10일 표결 결과에 달렸어요.',
  },
};

const generic = (name: string): DailyAnalysis => ({
  date: '2026-09-05', dates: DATES, headTitle: '오늘 발행',
  question: `${name}, 오늘 바뀐 것은 없어요`, dateline: DATELINE,
  now: 'neutral',
  synth: '어제와 같은 전망이에요. 새 사건이 생기면 다시 알려드려요.',
  axes: (['이슈', '차트', '매크로', '밸류', '수급'] as Axis[]).map((axis) => ({ axis, dir: 'neutral' as const, summary: '오늘 새로 확인된 것이 없어요.', hasPage: false })),
  title: '오늘 새로 확인된 것이 없어요',
  todayDate: '9월 5일', today: [],
  args: [], closingTitle: '지금 사도 될까요?', neg: [], pos: [],
  close: '전망을 바꿀 사건이 없어 어제 판단을 유지해요.',
});
export const dailyOf = (code: string, name: string) => DAILY[code] ?? generic(name);

export const FACTORS: Record<string, Partial<Record<Axis, FactorPage>>> = {
  DEFN: {
    '이슈': {
      axis: '이슈', dir: 'help', headline: '수주·수익성은 좋지만 밸류 부담이 남아 있습니다.',
      events: [
        { dir: 'help', k: '유럽 무기 주문 3분기 연속 증가', body: '전차·자주포를 만드는 한화에어로스페이스가 5년 치 주문을 받았어요. 이 ETF의 20%예요. 폴란드·루마니아 국방 예산에 추가 주문이 들어 있어요.' },
        { dir: 'help', k: '한화에어로 이익률 11.4%', body: '공장을 더 돌리면 무기 하나당 비용이 줄어요. 그래서 남는 돈이 늘어요. 이익률 12%에 도전해요. 유로 환율(1,460원)도 이익을 1%p 높여줘요.' },
        { dir: 'help', k: '기관 사흘 연속 매수·최고가', body: '국내 기관이 방산주를 사며 이 ETF가 역대 최고가를 썼어요. 개인이 이익을 챙기려 팔면 기관이 받아줘요.' },
        { dir: 'burden', k: '유럽 예산 표결 9월 10일 대기', body: '한화에어로스페이스 주가는 이미 많이 올라 비싸요. 표결이 통과돼야 이 주가가 말이 돼요. 미뤄지면 빠져요. 한화에어로스페이스 주가가 1년 이익의 31배(PER 31)예요. 평소보다 40% 비싸요.' },
      ],
    },
    '차트': {
      axis: '차트', dir: 'help', headline: '주가가 재료보다 먼저 움직였어요.',
      chartInds: [
        { dir: 'help', name: '20일 이동평균 위', d: '주가가 최근 20일 평균보다 높아요. 사는 사람이 파는 사람보다 많다는 뜻이에요.' },
        { dir: 'help', name: '역대 최고가 갱신', d: '9월 4일 종가가 역대 최고가예요. 위에 물린 사람이 없어 팔 물량이 적어요.' },
        { dir: 'burden', name: '20일 +18%', d: '짧은 기간에 많이 올라 차익을 챙기려는 매물이 나올 수 있어요.' },
      ],
      judg: ['20일 평균선 아래로 내려오면 상승 흐름이 끊긴 거예요.', '표결 통과 뒤에도 거래량이 줄면 재료가 다 반영된 거예요.'],
    },
  },
  AXAI: {
    '이슈': {
      axis: '이슈', dir: 'help', headline: '수요는 확인됐고, 남은 상승 폭이 관건이에요.',
      events: [
        { dir: 'help', k: '서버용 D램 값 3개월 연속 상승', body: 'AI 서버를 만드는 회사들이 메모리를 더 주문했어요. 메모리를 만드는 SK하이닉스·삼성전자가 이 ETF의 36%예요.' },
        { dir: 'help', k: 'HBM4 고객 3곳 첫 출하', body: '샘플 공급은 시작일 뿐이에요. 9월 12일 공개되는 양산 수율이 80%를 넘어야 이익으로 이어져요.' },
        { dir: 'burden', k: '주가 20일 +18% 선반영', body: '시장도 같은 걸 봤어요. 그래서 수율이 기대에 못 미치면 빠져요.' },
      ],
    },
  },
};

export const METRICS: Record<string, Partial<Record<Axis, MetricPage>>> = {
  DEFN: {
    '매크로': {
      axis: '매크로', dir: 'help', verdict: '상승 쪽이에요', hasDetail: false,
      tiles: [
        { label: '원/달러', value: '1377원', dir: 'help', note: '수출 이익률을 좌우해요.', wide: true },
        { label: '산업 원자재', value: '-8%', dir: 'help' },
        { label: '국고채 10년', value: '3.20%' },
        { label: '미 10년', value: '3.70%' },
        { label: '브렌트유', value: '85달러' },
        { label: '다음 금리 결정', value: 'D-22' },
      ],
    },
    '밸류': {
      axis: '밸류', dir: 'burden', verdict: '평소보다 비싸요', hasDetail: true,
      tiles: [
        { label: '한화에어로 PER', value: '31배', dir: 'burden', note: '1년 이익의 31배예요. 평소보다 40% 비싸요.', wide: true },
        { label: '이익률', value: '11.4%', dir: 'help' },
        { label: '수주 잔고', value: '5년 치', dir: 'help' },
      ],
    },
  },
  AXAI: {
    '밸류': {
      axis: '밸류', dir: 'burden', verdict: '평소보다 비싸요', hasDetail: false,
      tiles: [
        { label: 'SK하이닉스 PER', value: '12배', dir: 'burden', note: '1년 이익의 12배예요. 평소 8배보다 높아요.', wide: true },
        { label: '영업이익률', value: '49%', dir: 'help' },
        { label: '20일 상승', value: '+18%', dir: 'burden' },
      ],
    },
    '매크로': {
      axis: '매크로', dir: 'neutral', verdict: '중립이에요', hasDetail: false,
      tiles: [
        { label: '원/달러', value: '1377원', note: '수출 이익률을 좌우해요.', wide: true },
        { label: '국고채 10년', value: '3.20%' },
        { label: '미 10년', value: '3.70%' },
      ],
    },
  },
};

export const HINTS: Record<string, Hint> = {
  edge: {
    title: '분석 기준과 출처',
    body: '언론사 70곳의 뉴스·공시·리포트를 매일 모아 다섯 기준으로 정리해요. 전망 스티커는 다섯 기준을 합쳐 정해요.',
    list: [
      { k: '이슈', d: '재료가 된 사건이 진짜인가' },
      { k: '차트', d: '주가가 재료를 따라왔나' },
      { k: '매크로', d: '금리·환율이 밖에서 방해하나' },
      { k: '밸류', d: '이익 대비 주가가 싼가' },
      { k: '수급', d: '큰손이 믿고 사나' },
    ],
    why: '분석은 투자 판단을 돕는 참고 자료예요. 매수·매도 권유가 아니에요.',
  },
  '이슈': { title: '이슈', orig: '호재', body: '전망의 출발점이 된 사건이에요. 사건이 실제로 확인됐는지, 이 ETF 안의 어느 회사가 돈을 버는지를 봐요.' },
  '차트': { title: '차트', body: '주가가 재료를 얼마나 먼저 반영했는지 봐요. 많이 올랐으면 남은 폭이 줄어요.' },
  '매크로': { title: '매크로', body: '금리·환율·원자재처럼 회사 밖에서 이익을 흔드는 것을 봐요.' },
  '밸류': { title: '밸류', body: '주요 구성 종목 주가가 1년 이익의 몇 배인지 봐요. 평소보다 높으면 비싼 거예요.' },
  '수급': { title: '수급', body: '외국인·기관 같은 큰손이 사는지 파는지 봐요.' },
};
