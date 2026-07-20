/* applications 도메인 — mock 구현 (가상 증권사 "한빛투자증권" 기준)
 *
 * mock 도 반드시 Promise 를 반환한다(async). mock 과 real 의 로딩/에러 처리
 * 모양을 동일하게 유지하기 위함이다. 키·웹훅은 조직 전역(앱별 아님)으로 둔다.
 */
import type { ApplicationsRepository } from './repository';
import type {
  Application,
  WidgetKey,
  Webhook,
  CreateAppInput,
  IssueKeyInput,
  AddWebhookInput,
} from './types';

/** 네트워크 지연을 흉내내 로딩 상태가 실제처럼 동작하게 한다. */
function delay<T>(value: T, ms = 280): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms));
}

const SEED_APPS: Application[] = [
  { id: 'app_mts', slug: 'mts', name: '한빛 MTS', type: '모바일', env: 'production', calls: '1.24M', callsErr: '0.21%', mau: '84,200', p95: '142ms', keys: 2, hooks: 2 },
  { id: 'app_web', slug: 'web', name: '한빛 웹트레이딩', type: '웹', env: 'production', calls: '612K', callsErr: '0.34%', mau: '41,800', p95: '138ms', keys: 1, hooks: 1 },
  { id: 'app_lab', slug: 'lab', name: '리서치랩 (베타)', type: '웹', env: 'staging', calls: '48K', callsErr: '1.02%', mau: '1,120', p95: '210ms', keys: 1, hooks: 1 },
];

const SEED_KEYS: WidgetKey[] = [
  { key: 'pk_live_3f9a…d21', label: 'MTS · 운영', scope: 'analysis:read', env: 'live', created: '2026-04-12', used: '2분 전' },
  { key: 'pk_live_77c1…8be', label: '웹 · 운영', scope: 'analysis:read, widget', env: 'live', created: '2026-03-02', used: '11분 전' },
  { key: 'pk_test_a02e…11f', label: '리서치랩 · 테스트', scope: 'analysis:read', env: 'test', created: '2026-05-28', used: '1일 전' },
];

const SEED_WEBHOOKS: Webhook[] = [
  { url: 'https://api.hanbit.co.kr/edge/hooks', events: 'analysis.created, analysis.updated', status: '활성', rate: '99.8%' },
  { url: 'https://ops.hanbit.co.kr/alerts', events: 'error.spike', status: '활성', rate: '100%' },
  { url: 'https://staging.hanbit.co.kr/hooks', events: 'analysis.created', status: '일시정지', rate: '94.1%' },
];

const SEED_UNIVERSE = ['KODEX 200', 'KODEX 반도체', 'TIGER 2차전지테마', 'TIGER 미국S&P500', 'KODEX 은행'];

/** 모듈 수명 동안 유지되는 in-memory 상태 (생성/발급 시 누적). */
const apps: Application[] = [...SEED_APPS];
const keys: WidgetKey[] = [...SEED_KEYS];
const webhooks: Webhook[] = [...SEED_WEBHOOKS];

/** 16진 4자리 랜덤 조각 (앱 코드 런타임이므로 Math.random 허용). */
function rand(len: number): string {
  return Math.random().toString(16).slice(2, 2 + len);
}

export const mockApplicationsRepository: ApplicationsRepository = {
  listApps() {
    return delay(apps.map((a) => ({ ...a })));
  },

  getApp(slug: string) {
    const found = apps.find((a) => a.slug === slug);
    return delay<Application | null>(found ? { ...found } : null);
  },

  createApp({ name, type, env }: CreateAppInput) {
    const slug = (name.toLowerCase().replace(/[^a-z0-9]/g, '').slice(0, 8)) || 'app';
    const created: Application = {
      id: `app_${slug}`,
      slug,
      name,
      type,
      env,
      calls: '0',
      callsErr: '0%',
      mau: '—',
      p95: '—',
      keys: 0,
      hooks: 0,
    };
    apps.push(created);
    return delay({ ...created });
  },

  listKeys() {
    return delay(keys.map((k) => ({ ...k })));
  },

  issueKey({ label, env, scope }: IssueKeyInput) {
    const created: WidgetKey = {
      key: (env === 'live' ? 'pk_live_' : 'pk_test_') + rand(4) + '…' + rand(3),
      label,
      scope,
      env,
      created: '방금',
      used: '—',
    };
    keys.push(created);
    return delay({ ...created });
  },

  regenerateKey(key: string) {
    const target = keys.find((k) => k.key === key);
    if (!target) {
      return Promise.reject(new Error('키를 찾을 수 없습니다'));
    }
    target.key = (target.env === 'live' ? 'pk_live_' : 'pk_test_') + rand(4) + '…' + rand(3);
    target.used = '방금';
    return delay({ ...target });
  },

  revokeKey(key: string) {
    const idx = keys.findIndex((k) => k.key === key);
    if (idx >= 0) keys.splice(idx, 1);
    return delay<void>(undefined);
  },

  listWebhooks() {
    return delay(webhooks.map((w) => ({ ...w })));
  },

  addWebhook({ url, events }: AddWebhookInput) {
    const created: Webhook = { url, events, status: '활성', rate: '100%' };
    webhooks.push(created);
    return delay({ ...created });
  },

  getUniverse() {
    return delay([...SEED_UNIVERSE]);
  },
};
