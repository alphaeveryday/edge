/* screening 도메인 — mock 구현. 시안(v0.2) 목데이터 이식. */
import type { ScreeningRepository } from './repository';
import type { AutoPublishCriteria, BannedWord } from './types';

const words: BannedWord[] = [
  { id: 1, text: '급등 확실', risk: 'HIGH', action: 'BLOCK', active: true, registeredAt: '2026-05-02' },
  { id: 2, text: '무조건', risk: 'HIGH', action: 'BLOCK', active: true, registeredAt: '2026-05-02' },
  { id: 3, text: '매수 추천', risk: 'HIGH', action: 'BLOCK', active: true, registeredAt: '2026-05-14' },
  { id: 4, text: '목표가 돌파 예정', risk: 'MEDIUM', action: 'REVIEW', active: true, registeredAt: '2026-06-01' },
  { id: 5, text: '확실시', risk: 'MEDIUM', action: 'REVIEW', active: true, registeredAt: '2026-06-18' },
  { id: 6, text: '전량 매도', risk: 'HIGH', action: 'BLOCK', active: false, registeredAt: '2026-04-20' },
];
let nextWordId = 7;

let criteria: AutoPublishCriteria = { minSources: 2, maxRisk: 'MEDIUM' };

let disclaimer =
  '본 설명은 뉴스·공시 등 공개 데이터를 기반으로 자동 생성된 참고 정보이며, 특정 종목의 매수·매도를 권유하지 않습니다. 투자 판단과 책임은 투자자 본인에게 있습니다.';

export const mockScreeningRepository: ScreeningRepository = {
  async listWords(): Promise<BannedWord[]> {
    return words.map((w) => ({ ...w }));
  },
  async addWord(word) {
    words.unshift({ id: nextWordId++, ...word, active: true, registeredAt: '2026-07-21' });
  },
  async toggleWord(id) {
    const w = words.find((x) => x.id === id);
    if (!w) throw new Error('금칙어를 찾을 수 없습니다.');
    w.active = !w.active;
  },
  async getCriteria(): Promise<AutoPublishCriteria> {
    return { ...criteria };
  },
  async updateCriteria(patch) {
    criteria = { ...criteria, ...patch };
  },
  async getDisclaimer(): Promise<string> {
    return disclaimer;
  },
  async updateDisclaimer(text) {
    disclaimer = text;
  },
};
