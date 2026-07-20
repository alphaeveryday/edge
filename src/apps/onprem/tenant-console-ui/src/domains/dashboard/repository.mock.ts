/* dashboard 도메인 — mock 구현 (가상 증권사 "한빛투자증권" 기준)
 *
 * mock 도 반드시 Promise 를 반환한다(async). mock 과 real 의 로딩/에러 처리
 * 모양을 동일하게 유지하기 위함이다.
 */
import type { DashboardRepository } from './repository';
import type { DashboardOverview } from './types';

/** 네트워크 지연을 흉내내 로딩 상태가 실제처럼 동작하게 한다. */
function delay<T>(value: T, ms = 280): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms));
}

const SEED_OVERVIEW: DashboardOverview = {
  kpis: [
    { k: '위젯 호출 (오늘)', v: '1.9M', d: '▲ 6.2%', note: '전일 대비', tone: 'up' },
    { k: 'MAU', v: '127K', d: '▲ 3.1%', note: '전월 대비', tone: 'up' },
    { k: '에러율 (P95)', v: '0.28%', d: '▼ 0.04%p', note: '안정', tone: 'good' },
    { k: '분석 출력 (오늘)', v: '8,420', d: '▲ 2.4%', note: '전일 대비', tone: 'up' },
  ],
  callSeries: {
    weekly: [1.42, 1.55, 1.48, 1.71, 1.63, 1.82, 1.9],
    monthly: [
      1.2, 1.3, 1.25, 1.4, 1.35, 1.5, 1.45, 1.6, 1.55, 1.7, 1.65, 1.5, 1.62, 1.58, 1.72, 1.68, 1.8,
      1.74, 1.66, 1.78, 1.7, 1.84, 1.79, 1.71, 1.83, 1.77, 1.69, 1.81, 1.86, 1.9,
    ],
    weekLabels: ['월', '화', '수', '목', '금', '토', '일'],
  },
  mauByApp: [
    { name: '한빛 MTS', mau: '84,200', d: '▲ 2.8%' },
    { name: '한빛 웹트레이딩', mau: '41,800', d: '▲ 4.1%' },
    { name: '리서치랩 (베타)', mau: '1,120', d: '▲ 22%' },
  ],
  events: [
    { id: 'E-2841', title: '삼성전자, 3분기 잠정 실적 공시', etf: 'KODEX 반도체', impact: 72, t: '3분 전', cat: '실적' },
    { id: 'E-2840', title: '금융위, 2차전지 보조금 개편안 행정예고', etf: 'TIGER 2차전지테마', impact: 64, t: '11분 전', cat: '정책' },
    { id: 'E-2839', title: 'SK하이닉스, HBM4 양산 일정 단축', etf: 'KODEX 반도체', impact: 68, t: '18분 전', cat: '공시' },
    { id: 'E-2838', title: '한국은행 기준금리 동결 (3.25%)', etf: 'KODEX 은행', impact: 41, t: '34분 전', cat: '거시' },
    { id: 'E-2835', title: '코스피200 정기변경 발표', etf: 'KODEX 200', impact: 39, t: '1시간 전', cat: '지수' },
    { id: 'E-2833', title: '엔비디아 실적 서프라이즈, 반도체 동반 강세', etf: 'KODEX 반도체', impact: 58, t: '2시간 전', cat: '실적' },
  ],
  apps: [
    { id: 'app_mts', slug: 'mts', name: '한빛 MTS', type: '모바일', env: 'production', calls: '1.24M', callsErr: '0.21%' },
    { id: 'app_web', slug: 'web', name: '한빛 웹트레이딩', type: '웹', env: 'production', calls: '612K', callsErr: '0.34%' },
    { id: 'app_lab', slug: 'lab', name: '리서치랩 (베타)', type: '웹', env: 'staging', calls: '48K', callsErr: '1.02%' },
  ],
  complianceSummary: [
    { label: '오늘 분석 출력', value: '8,420건', tone: 'neutral' },
    { label: '키워드 차단', value: '14건', tone: 'bad' },
    { label: '면책문구 버전', value: 'v4 (최신)', tone: 'ok' },
  ],
};

export const mockDashboardRepository: DashboardRepository = {
  getOverview() {
    // 깊은 복사로 호출 측의 변형이 seed 에 새지 않게 한다.
    return delay<DashboardOverview>({
      ...SEED_OVERVIEW,
      kpis: SEED_OVERVIEW.kpis.map((x) => ({ ...x })),
      callSeries: {
        weekly: [...SEED_OVERVIEW.callSeries.weekly],
        monthly: [...SEED_OVERVIEW.callSeries.monthly],
        weekLabels: [...SEED_OVERVIEW.callSeries.weekLabels],
      },
      mauByApp: SEED_OVERVIEW.mauByApp.map((x) => ({ ...x })),
      events: SEED_OVERVIEW.events.map((x) => ({ ...x })),
      apps: SEED_OVERVIEW.apps.map((x) => ({ ...x })),
      complianceSummary: SEED_OVERVIEW.complianceSummary.map((x) => ({ ...x })),
    });
  },
};
