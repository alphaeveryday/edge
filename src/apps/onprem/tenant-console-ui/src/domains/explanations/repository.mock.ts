/* explanations 도메인 — mock 구현. 시안(v0.2) 목데이터 이식.
 * 모듈 레벨 가변 스토어라 mutation → invalidate → refetch 흐름이 real과 동일하게 동작한다. */
import type { ExplanationsRepository } from './repository';
import type { Explanation, FeedStatus } from './types';

const items: Explanation[] = [
  {
    id: 1, name: '삼성전자', code: '005930', market: 'KRX', direction: 1, changePct: 3.24,
    status: 'AUTO_PUBLISHED', risk: 'LOW', receivedRelative: '9분 전', receivedAt: '2026-07-11 10:42 KST',
    evidence: [
      { type: '공시', title: '3분기 잠정 실적 발표 — 영업이익 컨센서스 12% 상회', source: 'KIND', time: '10:31' },
      { type: '뉴스', title: '삼성전자, 반도체 부문 회복세 뚜렷', source: '연합인포맥스', time: '10:38' },
    ],
    original: '3분기 잠정 영업이익이 시장 예상치를 12% 상회하는 공시가 발표된 이후 기관 중심의 매수세가 유입되며 상승했습니다.',
    final: '3분기 잠정 영업이익이 시장 예상치를 12% 상회하는 공시가 발표된 이후 기관 중심의 매수세가 유입되며 상승했습니다.',
  },
  {
    id: 2, name: '에코프로비엠', code: '247540', market: 'KRX', direction: 1, changePct: 8.91,
    status: 'REVIEW_REQUIRED', risk: 'HIGH', reviewReason: 'ASSERTIVE',
    receivedRelative: '14분 전', receivedAt: '2026-07-11 10:37 KST',
    evidence: [
      { type: '공시', title: '북미 완성차 업체와 2차전지 양극재 장기 공급계약 체결', source: 'KIND', time: '10:22' },
      { type: '뉴스', title: '에코프로비엠, 대규모 수주 소식에 급등', source: '이데일리', time: '10:29' },
    ],
    original: '북미 완성차 업체와의 대규모 양극재 공급계약 공시로 급등했으며, 추가 상승이 확실시됩니다.',
    final: '북미 완성차 업체와의 대규모 양극재 공급계약 공시로 급등했으며, 추가 상승이 확실시됩니다.',
  },
  {
    id: 3, name: 'SK하이닉스', code: '000660', market: 'KRX', direction: -1, changePct: 2.15,
    status: 'AUTO_PUBLISHED', risk: 'LOW', receivedRelative: '22분 전', receivedAt: '2026-07-11 10:29 KST',
    evidence: [
      { type: '뉴스', title: '필라델피아 반도체지수 1.8% 하락 마감', source: '연합인포맥스', time: '08:10' },
      { type: '뉴스', title: '외국인, 반도체 대형주 차익 실현 매도', source: '머니투데이', time: '10:05' },
    ],
    original: '간밤 필라델피아 반도체지수 하락과 외국인 차익 실현 매물이 겹치며 하락했습니다.',
    final: '간밤 필라델피아 반도체지수 하락과 외국인 차익 실현 매물이 겹치며 하락했습니다.',
  },
  {
    id: 4, name: 'NVIDIA', code: 'NVDA', market: 'NASDAQ', direction: 1, changePct: 4.87,
    status: 'AUTO_PUBLISHED', risk: 'MEDIUM', receivedRelative: '38분 전', receivedAt: '2026-07-11 10:13 KST',
    evidence: [
      { type: '공시', title: '차세대 AI 가속기 출하 일정 발표 (8-K)', source: 'SEC EDGAR', time: '05:12' },
      { type: '뉴스', title: 'NVIDIA, 신제품 수요 전망 상향에 강세', source: 'Reuters', time: '05:40' },
    ],
    original: '차세대 AI 가속기 출하 일정 공시와 수요 전망 상향 보도가 이어지며 상승했습니다.',
    final: '차세대 AI 가속기 출하 일정 공시와 수요 전망 상향 보도가 이어지며 상승했습니다.',
  },
  {
    id: 5, name: '카카오', code: '035720', market: 'KRX', direction: -1, changePct: 5.62,
    status: 'REVIEW_REQUIRED', risk: 'MEDIUM', reviewReason: 'SINGLE_SOURCE',
    receivedRelative: '41분 전', receivedAt: '2026-07-11 10:10 KST',
    evidence: [
      { type: '뉴스', title: '카카오, 주요 계열사 매각 검토설에 약세', source: '단독 보도 (1개 매체)', time: '09:52' },
    ],
    original: '주요 계열사 매각 검토 보도가 나오면서 투자 심리가 위축되어 하락했습니다.',
    final: '주요 계열사 매각 검토 보도가 나오면서 투자 심리가 위축되어 하락했습니다.',
  },
  {
    id: 6, name: 'Tesla', code: 'TSLA', market: 'NASDAQ', direction: -1, changePct: 6.33,
    status: 'BLOCKED', risk: 'HIGH', reviewReason: 'BANNED_WORD',
    receivedRelative: '1시간 전', receivedAt: '2026-07-11 09:48 KST',
    evidence: [
      { type: '뉴스', title: '2분기 인도량 시장 예상치 하회', source: 'Bloomberg', time: '05:02' },
    ],
    original: '2분기 인도량 부진으로 급락했으며, 반등 전까지 전량 매도가 유리합니다.',
    final: '2분기 인도량 부진으로 급락했으며, 반등 전까지 전량 매도가 유리합니다.',
  },
  {
    id: 7, name: '셀트리온', code: '068270', market: 'KRX', direction: 1, changePct: 2.05,
    status: 'AUTO_PUBLISHED', risk: 'LOW', receivedRelative: '1시간 전', receivedAt: '2026-07-11 09:41 KST',
    evidence: [
      { type: '공시', title: '바이오시밀러 신제품 유럽 판매 승인', source: 'KIND', time: '09:02' },
      { type: '뉴스', title: '셀트리온, 유럽 승인 소식에 상승', source: '한국경제', time: '09:15' },
    ],
    original: '바이오시밀러 신제품의 유럽 판매 승인 공시 이후 매수세가 유입되며 상승했습니다.',
    final: '바이오시밀러 신제품의 유럽 판매 승인 공시 이후 매수세가 유입되며 상승했습니다.',
  },
  {
    id: 8, name: 'Apple', code: 'AAPL', market: 'NASDAQ', direction: 1, changePct: 1.12,
    status: 'AUTO_PUBLISHED', risk: 'LOW', receivedRelative: '2시간 전', receivedAt: '2026-07-11 08:55 KST',
    evidence: [
      { type: '뉴스', title: '서비스 부문 매출 성장 지속 전망', source: 'WSJ', time: '05:20' },
      { type: '뉴스', title: '애플, 기관 매수세에 소폭 상승', source: 'Reuters', time: '05:45' },
    ],
    original: '서비스 부문 매출 성장 전망 보도와 기관 매수세에 힘입어 소폭 상승했습니다.',
    final: '서비스 부문 매출 성장 전망 보도와 기관 매수세에 힘입어 소폭 상승했습니다.',
  },
  {
    id: 9, name: '포스코퓨처엠', code: '003670', market: 'KRX', direction: -1, changePct: 4.48,
    status: 'REVIEW_REQUIRED', risk: 'HIGH', reviewReason: 'BANNED_WORD',
    receivedRelative: '2시간 전', receivedAt: '2026-07-11 08:47 KST',
    evidence: [
      { type: '뉴스', title: '리튬 가격 하락에 2차전지 소재주 동반 약세', source: '연합인포맥스', time: '08:12' },
      { type: '뉴스', title: '포스코퓨처엠, 원재료 가격 부담 지속', source: '서울경제', time: '08:30' },
    ],
    original: '리튬 가격 하락으로 소재주 전반이 약세를 보였으며, 목표가 돌파 예정 시점은 지연될 전망입니다.',
    final: '리튬 가격 하락으로 소재주 전반이 약세를 보였으며, 목표가 돌파 예정 시점은 지연될 전망입니다.',
  },
  {
    id: 10, name: '두산에너빌리티', code: '034020', market: 'KRX', direction: 1, changePct: 6.74,
    status: 'AUTO_PUBLISHED', risk: 'MEDIUM', receivedRelative: '3시간 전', receivedAt: '2026-07-11 07:58 KST',
    evidence: [
      { type: '공시', title: '해외 원전 기자재 수주 계약 체결', source: 'KIND', time: '07:31' },
      { type: '뉴스', title: '두산에너빌리티, 원전 수주 모멘텀 지속', source: '매일경제', time: '07:44' },
    ],
    original: '해외 원전 기자재 수주 공시가 발표되며 원전 관련 모멘텀이 부각되어 상승했습니다.',
    final: '해외 원전 기자재 수주 공시가 발표되며 원전 관련 모멘텀이 부각되어 상승했습니다.',
  },
  {
    id: 11, name: 'AMD', code: 'AMD', market: 'NASDAQ', direction: -1, changePct: 3.29,
    status: 'UNPUBLISHED', risk: 'MEDIUM', receivedRelative: '4시간 전', receivedAt: '2026-07-11 06:50 KST',
    evidence: [
      { type: '뉴스', title: '데이터센터 부문 경쟁 심화 우려', source: 'Reuters', time: '05:31' },
    ],
    original: '데이터센터 부문 경쟁 심화 우려가 제기되며 하락했습니다.',
    final: '데이터센터 부문 경쟁 심화 우려가 제기되며 하락했습니다.',
  },
  {
    id: 13, name: 'Rivian', code: 'RIVN', market: 'NASDAQ', direction: 1, changePct: 11.42,
    status: 'REJECTED', risk: 'HIGH', reviewReason: 'ASSERTIVE',
    receivedRelative: '5시간 전', receivedAt: '2026-07-11 05:40 KST',
    evidence: [
      { type: '뉴스', title: '신형 SUV 사전예약 개시, 초기 반응 호조', source: 'TechCrunch', time: '04:55' },
    ],
    original: '신형 SUV 사전예약 개시 소식에 급등했으며, 강한 상승세가 계속될 것입니다.',
    final: '신형 SUV 사전예약 개시 소식에 급등했으며, 강한 상승세가 계속될 것입니다.',
  },
  {
    id: 12, name: 'LG에너지솔루션', code: '373220', market: 'KRX', direction: 1, changePct: 1.86,
    status: 'AUTO_PUBLISHED', risk: 'LOW', receivedRelative: '4시간 전', receivedAt: '2026-07-11 06:32 KST',
    evidence: [
      { type: '공시', title: '유럽 배터리 합작법인 증설 투자 결정', source: 'KIND', time: '06:02' },
      { type: '뉴스', title: 'LG엔솔, 유럽 증설 투자에 강세', source: '한국경제', time: '06:15' },
    ],
    original: '유럽 배터리 합작법인 증설 투자 공시가 발표된 이후 매수세가 유입되며 상승했습니다.',
    final: '유럽 배터리 합작법인 증설 투자 공시가 발표된 이후 매수세가 유입되며 상승했습니다.',
  },
];

function patch(id: number, updater: (it: Explanation) => Explanation): void {
  const idx = items.findIndex((it) => it.id === id);
  // real 구현의 404와 같은 의미로 실패를 드러낸다 — 없는 ID에 성공 토스트가 뜨지 않게
  if (idx < 0) throw new Error('가격 변동 설명을 찾을 수 없습니다.');
  items[idx] = updater(items[idx]);
}

export const mockExplanationsRepository: ExplanationsRepository = {
  async list(): Promise<Explanation[]> {
    // 참조 공유로 인한 캐시 오염을 막기 위해 스냅샷을 반환한다
    return items.map((it) => ({ ...it, evidence: [...it.evidence] }));
  },
  async feedStatus(): Promise<FeedStatus> {
    return { state: 'NORMAL', lastReceivedRelative: '2분 전', todayReceived: 128 };
  },
  async updateFinal(id, final) {
    patch(id, (it) => ({ ...it, final }));
  },
  async stop(id) {
    patch(id, (it) => ({ ...it, status: 'UNPUBLISHED' }));
  },
  async moveToReview(id) {
    patch(id, (it) => ({ ...it, status: 'REVIEW_REQUIRED' }));
  },
  async approve(id, final, _note) {
    // 검수자 승인은 자동 제공과 구분되는 APPROVED — 상태기계(state-machine.md) 어휘
    patch(id, (it) => ({ ...it, status: 'APPROVED', final, reviewReason: undefined }));
  },
  async reject(id, _note) {
    patch(id, (it) => ({ ...it, status: 'REJECTED' }));
  },
  async saveDraft(id, final) {
    patch(id, (it) => ({ ...it, final }));
  },
};
