/* usage 도메인 — mock 구현 (가상 증권사 "한빛투자증권" 기준)
 *
 * mock 도 반드시 Promise 를 반환한다(async). mock 과 real 의 로딩/에러 처리
 * 모양을 동일하게 유지하기 위함이다.
 */
import type { UsageRepository } from './repository';
import type { UsageReport } from './types';

/** 네트워크 지연을 흉내내 로딩 상태가 실제처럼 동작하게 한다. */
function delay<T>(value: T, ms = 280): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms));
}

const SEED_REPORT: UsageReport = {
  callSeries: {
    weekly: [1.42, 1.55, 1.48, 1.71, 1.63, 1.82, 1.9],
    monthly: [
      1.2, 1.3, 1.25, 1.4, 1.35, 1.5, 1.45, 1.6, 1.55, 1.7, 1.65, 1.5, 1.62, 1.58, 1.72, 1.68,
      1.8, 1.74, 1.66, 1.78, 1.7, 1.84, 1.79, 1.71, 1.83, 1.77, 1.69, 1.81, 1.86, 1.9,
    ],
    weekLabels: ['월', '화', '수', '목', '금', '토', '일'],
  },
  appShare: {
    rows: [
      [0.9, 0.45, 0.07],
      [0.98, 0.5, 0.07],
      [0.94, 0.47, 0.07],
      [1.1, 0.54, 0.07],
      [1.04, 0.52, 0.07],
      [1.16, 0.59, 0.07],
      [1.24, 0.59, 0.07],
    ],
    labels: ['월', '화', '수', '목', '금', '토', '일'],
    names: ['한빛 MTS', '한빛 웹트레이딩', '리서치랩'],
  },
  byApp: [
    { name: '한빛 MTS', calls: '1.24M', p95: '138ms', err: '0.21%', share: 64 },
    { name: '한빛 웹트레이딩', calls: '612K', p95: '138ms', err: '0.34%', share: 33 },
    { name: '리서치랩 (베타)', calls: '48K', p95: '210ms', err: '1.02%', share: 3 },
  ],
  mau: {
    months: [98, 104, 101, 112, 118, 121, 127],
    labels: ['12월', '1월', '2월', '3월', '4월', '5월', '6월'],
  },
};

/** seed 를 깊은 복사해 반환한다 (호출자가 mutate 해도 seed 가 오염되지 않게). */
function cloneReport(r: UsageReport): UsageReport {
  return {
    callSeries: {
      weekly: [...r.callSeries.weekly],
      monthly: [...r.callSeries.monthly],
      weekLabels: [...r.callSeries.weekLabels],
    },
    appShare: {
      rows: r.appShare.rows.map((row) => [...row]),
      labels: [...r.appShare.labels],
      names: [...r.appShare.names],
    },
    byApp: r.byApp.map((a) => ({ ...a })),
    mau: {
      months: [...r.mau.months],
      labels: [...r.mau.labels],
    },
  };
}

export const mockUsageRepository: UsageRepository = {
  getUsage() {
    return delay(cloneReport(SEED_REPORT));
  },
};
