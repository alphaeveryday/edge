/* dashboard 도메인 — mock·real 이 공유하는 타입 */

/** KPI 카드 (StatCard 에 그대로 전개된다) */
export interface DashboardKpi {
  k: string;
  v: string;
  d: string;
  note: string;
  tone: 'up' | 'down' | 'good' | 'warn' | 'flat';
}

/** 위젯 호출 추이 시계열 (주간/월간 + 주간 라벨) */
export interface CallSeries {
  weekly: number[];
  monthly: number[];
  weekLabels: string[];
}

/** 앱별 MAU 요약 */
export interface MauByApp {
  name: string;
  mau: string;
  d: string;
}

/** 최근 분석 이벤트 */
export interface DashboardEvent {
  id: string;
  title: string;
  etf: string;
  impact: number;
  t: string;
  cat: string;
}

/** 애플리케이션 요약 (대시보드 목록용) */
export interface AppSummary {
  id: string;
  slug: string;
  name: string;
  type: '모바일' | '웹';
  env: string;
  calls: string;
  callsErr: string;
}

/** 컴플라이언스 요약 행 */
export interface ComplianceSummaryRow {
  label: string;
  value: string;
  tone: 'neutral' | 'bad' | 'warn' | 'ok';
}

/** 대시보드 한 화면을 구성하는 복합 데이터 */
export interface DashboardOverview {
  kpis: DashboardKpi[];
  callSeries: CallSeries;
  mauByApp: MauByApp[];
  events: DashboardEvent[];
  apps: AppSummary[];
  complianceSummary: ComplianceSummaryRow[];
}
