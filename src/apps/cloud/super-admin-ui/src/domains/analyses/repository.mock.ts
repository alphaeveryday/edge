/* analyses 도메인 — mock 구현. 시안(v0.1) 목데이터 이식. */
import type { AnalysesRepository } from './repository';
import type { Analysis } from './types';

const analyses: Analysis[] = [
  {
    id: 'a1', name: '삼성전자', code: '005930', market: 'KRX', direction: 1, changePct: 3.42,
    status: 'COMPLETED', basisTime: '07-10 09:12', basisTimeAbs: '2026-07-10 09:12 KST',
    doneTime: '2026-07-10 09:14 KST', score: 78, corrected: false,
    result:
      '3분기 잠정 실적이 시장 컨센서스(영업이익 11.2조)를 14% 상회하며 반도체 부문 회복이 확인됨. HBM4 양산 일정 조기화 언급이 외국인 순매수를 견인, 개장 직후 대량 매수세로 이어진 것으로 분석됨.\n반도체 비중이 높은 ETF(KODEX 반도체, TIGER Fn반도체TOP10)에 직접적 상방 영향.',
    evidence: [
      { type: '공시', title: '삼성전자, 2026년 3분기 잠정 실적 공시 (연결 영업이익 12.8조)', source: 'KIND', time: '2026-07-10 08:32' },
      { type: '뉴스', title: '"HBM4 양산 앞당긴다"… 삼성전자, 하반기 공급 계약 확대', source: '연합인포맥스', time: '2026-07-10 08:51' },
      { type: '시세', title: '개장 후 10분간 거래대금 4,120억 · 외국인 순매수 1,840억', source: 'KRX 시세', time: '2026-07-10 09:12' },
    ],
  },
  {
    id: 'a2', name: 'SK하이닉스', code: '000660', market: 'KRX', direction: 1, changePct: 5.18,
    status: 'COMPLETED', basisTime: '07-10 09:31', basisTimeAbs: '2026-07-10 09:31 KST',
    doneTime: '2026-07-10 09:33 KST', score: 84, corrected: false,
    result:
      '삼성전자 호실적 공시에 따른 반도체 섹터 동반 강세에 더해, 엔비디아향 HBM 추가 수주 보도가 상승 폭을 확대함. 거래량이 20일 평균 대비 3.1배로 수급 쏠림이 뚜렷함.',
    evidence: [
      { type: '뉴스', title: 'SK하이닉스, 엔비디아 차세대 GPU향 HBM4 추가 수주 임박', source: '뉴스핌', time: '2026-07-10 09:05' },
      { type: '시세', title: '거래량 20일 평균 대비 312% · 기관 순매수 920억', source: 'KRX 시세', time: '2026-07-10 09:31' },
    ],
  },
  {
    id: 'a3', name: '에코프로비엠', code: '247540', market: 'KRX', direction: -1, changePct: 6.71,
    status: 'COMPLETED', basisTime: '07-10 10:05', basisTimeAbs: '2026-07-10 10:05 KST',
    doneTime: '2026-07-10 10:08 KST', score: 71, corrected: false,
    result:
      '주요 고객사의 북미 전기차 판매 부진 발표로 양극재 수주 축소 우려가 확산됨. 2차전지 섹터 전반의 동반 약세 속 낙폭 최대. 2차전지 테마 ETF의 하방 압력 요인.',
    evidence: [
      { type: '뉴스', title: '북미 전기차 2분기 판매 전년比 18% 감소… 배터리 소재주 급락', source: '연합인포맥스', time: '2026-07-10 09:48' },
      { type: '실적 / 재무', title: '에코프로비엠 2분기 영업이익 컨센서스 하향 (−22%)', source: 'DART 재무', time: '2026-07-10 09:55' },
    ],
  },
  {
    id: 'a4', name: '셀트리온', code: '068270', market: 'KRX', direction: -1, changePct: 3.05,
    status: 'PENDING', basisTime: '07-10 13:44', basisTimeAbs: '2026-07-10 13:44 KST',
    doneTime: '—', score: 0, corrected: false,
    result: '분석 대기 중입니다. 근거 데이터 수집이 완료되면 자동으로 분석이 시작됩니다.',
    evidence: [
      { type: '시세', title: '오후 급락 감지 · 거래량 20일 평균 대비 189%', source: 'KRX 시세', time: '2026-07-10 13:44' },
    ],
  },
  {
    id: 'a5', name: 'POSCO홀딩스', code: '005490', market: 'KRX', direction: 1, changePct: 4.12,
    status: 'COMPLETED', basisTime: '07-10 09:58', basisTimeAbs: '2026-07-10 09:58 KST',
    doneTime: '2026-07-10 10:01 KST', score: 65, corrected: false,
    result:
      '리튬 국제 가격 반등과 아르헨티나 염호 2단계 상업 생산 개시 공시가 겹치며 이차전지 소재 밸류체인 기대감이 재부각됨.',
    evidence: [
      { type: '공시', title: 'POSCO홀딩스, 아르헨티나 리튬 염호 2단계 상업 생산 개시', source: 'KIND', time: '2026-07-10 09:40' },
      { type: '뉴스', title: '탄산리튬 가격 3주 연속 상승… 소재주 반등 조짐', source: '뉴스핌', time: '2026-07-10 09:21' },
    ],
  },
  {
    id: 'a6', name: '카카오', code: '035720', market: 'KRX', direction: -1, changePct: 4.88,
    status: 'FAILED', basisTime: '07-10 11:20', basisTimeAbs: '2026-07-10 11:20 KST',
    doneTime: '—', score: 0, corrected: false,
    result:
      '분석 실패: 근거 데이터 수집 단계에서 뉴스 소스 응답 시간 초과(timeout)가 발생했습니다. 재시도 큐에 등록되어 있습니다.',
    evidence: [
      { type: '시세', title: '오전 급락 감지 · 공매도 잔고 비율 상승', source: 'KRX 시세', time: '2026-07-10 11:20' },
    ],
  },
  {
    id: 'a7', name: '두산에너빌리티', code: '034020', market: 'KRX', direction: 1, changePct: 7.35,
    status: 'COMPLETED', basisTime: '07-10 09:22', basisTimeAbs: '2026-07-10 09:22 KST',
    doneTime: '2026-07-10 09:25 KST', score: 88, corrected: false,
    result:
      '체코 신규 원전 2기 추가 수주 우선협상대상자 선정 보도로 개장 직후 급등. 원전 테마 전반으로 매수세가 확산되며 관련 ETF 상방 영향이 큼.',
    evidence: [
      { type: '뉴스', title: '두산에너빌리티, 체코 신규 원전 2기 우선협상대상자 선정', source: '연합인포맥스', time: '2026-07-10 08:47' },
      { type: '시세', title: '개장 동시호가 매수 잔량 급증 · 상한가 근접 후 되돌림', source: 'KRX 시세', time: '2026-07-10 09:22' },
    ],
  },
  {
    id: 'a8', name: 'LG에너지솔루션', code: '373220', market: 'KRX', direction: -1, changePct: 3.6,
    status: 'PENDING', basisTime: '07-10 14:10', basisTimeAbs: '2026-07-10 14:10 KST',
    doneTime: '—', score: 0, corrected: false,
    result: '분석 대기 중입니다. 근거 데이터 수집이 완료되면 자동으로 분석이 시작됩니다.',
    evidence: [
      { type: '시세', title: '오후 하락 감지 · 외국인 순매도 전환', source: 'KRX 시세', time: '2026-07-10 14:10' },
    ],
  },
  {
    id: 'a9', name: 'NVIDIA', code: 'NVDA', market: 'NASDAQ', direction: 1, changePct: 4.92,
    status: 'COMPLETED', basisTime: '07-11 05:12', basisTimeAbs: '2026-07-10 16:12 EDT (07-11 05:12 KST)',
    doneTime: '2026-07-11 05:15 KST', score: 82, corrected: false,
    result:
      '차세대 데이터센터 GPU 출하 가이던스 상향 보도와 대형 클라우드 3사의 CapEx 증액 발표가 겹치며 정규장 마감까지 강세 지속. 국내 상장 미국 반도체 ETF 및 TIGER 미국나스닥100에 상방 영향.',
    evidence: [
      { type: '뉴스', title: 'NVIDIA, next-gen GPU shipment guidance raised for H2', source: 'Reuters', time: '2026-07-10 14:02 EDT' },
      { type: '뉴스', title: '대형 클라우드 3사, AI 인프라 CapEx 일제 증액', source: '연합인포맥스', time: '2026-07-11 04:40 KST' },
      { type: '시세', title: '정규장 거래대금 $48.2B · 종가 기준 사상 최고가 경신', source: 'NASDAQ TotalView', time: '2026-07-10 16:00 EDT' },
    ],
  },
  {
    id: 'a10', name: 'Tesla', code: 'TSLA', market: 'NASDAQ', direction: -1, changePct: 5.44,
    status: 'COMPLETED', basisTime: '07-11 04:48', basisTimeAbs: '2026-07-10 15:48 EDT (07-11 04:48 KST)',
    doneTime: '2026-07-11 04:52 KST', score: 74, corrected: false,
    result:
      '2분기 인도량이 컨센서스를 9% 하회한 데 이어 주요 시장 가격 인하 발표로 마진 축소 우려가 부각됨. 장 마감 직전 낙폭 확대.',
    evidence: [
      { type: '실적 / 재무', title: 'Tesla Q2 deliveries miss consensus by 9%', source: 'SEC/IR', time: '2026-07-10 09:30 EDT' },
      { type: '뉴스', title: 'Tesla, 미국·유럽 주요 모델 가격 인하 발표', source: 'Reuters', time: '2026-07-10 15:20 EDT' },
    ],
  },
  {
    id: 'a11', name: 'Apple', code: 'AAPL', market: 'NASDAQ', direction: 1, changePct: 2.87,
    status: 'PENDING', basisTime: '07-11 05:00', basisTimeAbs: '2026-07-10 16:00 EDT (07-11 05:00 KST)',
    doneTime: '—', score: 0, corrected: false,
    result: '분석 대기 중입니다. 근거 데이터 수집이 완료되면 자동으로 분석이 시작됩니다.',
    evidence: [
      { type: '시세', title: '종가 기준 상승 감지 · 거래량 평균 대비 141%', source: 'NASDAQ TotalView', time: '2026-07-10 16:00 EDT' },
    ],
  },
  {
    id: 'a12', name: 'Super Micro Computer', code: 'SMCI', market: 'NASDAQ', direction: -1, changePct: 8.12,
    status: 'EXCLUDED', basisTime: '07-11 04:30', basisTimeAbs: '2026-07-10 15:30 EDT (07-11 04:30 KST)',
    doneTime: '2026-07-11 04:35 KST', score: 55, corrected: false,
    result:
      '회계 이슈 관련 미확인 보도로 급락. 근거 데이터의 신뢰도가 기준 미달로 판단되어 운영자가 분석 대상에서 제외함.',
    evidence: [
      { type: '뉴스', title: '회계 감사인 교체설 보도 (미확인)', source: 'SNS/커뮤니티', time: '2026-07-10 14:55 EDT' },
    ],
  },
];

function patch(id: string, updater: (a: Analysis) => Analysis): void {
  const idx = analyses.findIndex((a) => a.id === id);
  // real 구현의 404와 같은 의미로 실패를 드러낸다 — 없는 ID에 성공 토스트가 뜨지 않게
  if (idx < 0) throw new Error('분석 건을 찾을 수 없습니다.');
  analyses[idx] = updater(analyses[idx]);
}

export const mockAnalysesRepository: AnalysesRepository = {
  async list(): Promise<Analysis[]> {
    return analyses.map((a) => ({ ...a, evidence: [...a.evidence] }));
  },
  async correct(id, result) {
    patch(id, (a) => ({ ...a, result, corrected: true }));
  },
  async exclude(id) {
    patch(id, (a) => ({ ...a, status: 'EXCLUDED' }));
  },
  async restore(id) {
    patch(id, (a) => ({ ...a, status: 'COMPLETED' }));
  },
};
