import type { IssueDetail, IssueRow } from '../types';

export const ISSUE_ROWS: IssueRow[] = [
  { id: 'i1', rank: 1, delta: 2, title: 'SMR 누적 주문 월간 데이터, 전월 대비 개선', kw: 'SMR 누적 주문 개선 방향 유지' },
  { id: 'i2', rank: 2, delta: 1, title: '로봇 주문 증가율 월간 데이터, 전월 대비 개선', kw: '로봇 주문 증가율 개선 방향 유지' },
  { id: 'i3', rank: 3, delta: 0, title: 'SK하이닉스, HBM4 샘플 고객사 3곳 공급 개시', kw: '4분기 양산 관문 · 수율 80% 확인 전', etf: { code: 'AXAI', name: 'TIGER 반도체TOP10', theme: 'AI·반도체', logoBg: '#3D34E0', sub: '미래에셋 · 국내 반도체 상위 10종' } },
  { id: 'i4', rank: 4, delta: -2, title: '2나노 수율 월간 데이터, 전월 대비 개선', kw: '2나노 수율 개선 방향 유지' },
  { id: 'i5', rank: 5, delta: 1, title: '전기차 판매 증가율 월간 데이터, 전월 대비 부진', kw: '전기차 판매 증가율 개선 미확인' },
  { id: 'i6', rank: 6, delta: -1, title: '폴란드·루마니아 국방예산 9월 10일 표결', kw: '통과 시 K2·천무 추가 주문 공식화', etf: { code: 'DEFN', name: 'PLUS K방산', theme: '방산', logoBg: '#131318', sub: '한화 · 국내 방산 대표 10종' } },
  { id: 'i7', rank: 7, delta: 0, title: 'WTI 유가 월간 데이터, 전월 대비 부진', kw: 'WTI 유가 개선 미확인' },
];

export const ISSUE_DETAILS: Record<string, Omit<IssueDetail, 'affected'> & { affectedCodes: string[] }> = {
  i3: {
    id: 'i3',
    title: 'SK하이닉스가 HBM4 샘플을 고객사 3곳에 공급하기 시작했어요',
    body: '최대 구성종목 SK하이닉스가 고객사 3곳에 HBM4 샘플 공급을 시작했어요. 통상 샘플 공급 후 인증까지 2~3개월, 양산까지는 한 분기가 더 걸려요. 회사가 밝힌 목표는 4분기 양산이에요.',
    points: ['샘플 공급 자체는 매출이 아니에요. 인증을 통과해 양산 물량이 잡혀야 이익으로 연결돼요.', '시장의 시선은 9월 12일 공개될 양산 수율로 옮겨가 있어요. 80%를 넘으면 4분기 실적 상향이 가능해져요.'],
    sources: [
      { title: 'SK하이닉스, HBM4 샘플 고객 3곳 출하…4분기 양산 목표', pub: '한국경제 · 오늘 07:10', url: 'https://example.com/1' },
      { title: '[공시] SK하이닉스 HBM4 개발 완료 및 고객 인증 진행', pub: '금융감독원 전자공시 · 오늘 06:30', url: 'https://example.com/2' },
      { title: 'HBM4 인증 일정과 수율 리스크 점검', pub: '삼성증권 리포트 · 어제', url: 'https://example.com/3' },
    ],
    effect: { theme: 'AI·반도체', dir: 'help', body: '세부 내용과 후속 발표 확인 전이에요. 결과가 확인되면 AI·반도체 테마의 현재 전망과 관련 ETF 평가를 다시 확인해야 해요.' },
    affectedCodes: ['AXAI', 'GRID'],
  },
  i6: {
    id: 'i6',
    title: '폴란드·루마니아 국방 예산 표결이 9월 10일이에요',
    body: '유럽이 3분기 연속 무기 주문을 늘렸어요. 폴란드·루마니아 국방 예산에 전차·자주포 추가 주문이 들어 있고, 9월 10일 의회 표결로 확정돼요.',
    points: ['통과되면 K2 전차·천무 추가 주문이 공식화돼요. 전차·자주포 회사가 PLUS K방산의 38%예요.', '미뤄지면 이미 오른 주가가 빠질 수 있어요. 한화에어로스페이스 주가가 1년 이익의 31배예요.'],
    sources: [
      { title: '폴란드 의회, 국방예산 증액안 9월 10일 표결', pub: '연합뉴스 · 오늘 08:00', url: 'https://example.com/4' },
      { title: '루마니아, K9 자주포 추가 도입 예산 편성', pub: '매일경제 · 어제', url: 'https://example.com/5' },
    ],
    effect: { theme: '방산', dir: 'help', body: '표결 결과에 따라 방산 테마 전망이 갈려요. 통과 전까지는 현재 전망을 유지해요.' },
    affectedCodes: ['DEFN'],
  },
};

export const genericIssue = (row: IssueRow): Omit<IssueDetail, 'affected'> & { affectedCodes: string[] } => ({
  id: row.id,
  title: row.title,
  body: `${row.kw}. 세부 내용은 다음 발표에서 확인돼요.`,
  points: ['숫자와 일정이 확정되면 이 이슈의 범위와 지속성을 더 정확히 판단할 수 있어요.'],
  sources: [{ title: row.title, pub: '연합뉴스 · 오늘', url: 'https://example.com/0' }],
  effect: { theme: 'AI·반도체', dir: 'neutral', body: '세부 내용과 후속 발표 확인 전이에요. 결과가 확인되면 관련 ETF 평가를 다시 확인해야 해요.' },
  affectedCodes: ['AXAI'],
});
