import { Platform } from 'react-native';
import * as SecureStore from 'expo-secure-store';
import { CancelledError } from '@tanstack/react-query';

// 키체인과 웹 저장소에 두는 토큰과 기기 상태
const KEYS = { access: 'etforca.access', refresh: 'etforca.refresh', device: 'etforca.device', onboarded: 'etforca.onboarded', disclaimed: 'etforca.disclaimed', theme: 'etforca.theme' } as const;
type Key = keyof typeof KEYS;

const get = async (k: Key): Promise<string | null> => {
  if (Platform.OS === 'web') return globalThis.localStorage?.getItem(KEYS[k]) ?? null;
  return SecureStore.getItemAsync(KEYS[k]);
};
const set = async (k: Key, v: string | null) => {
  if (Platform.OS === 'web') {
    if (v === null) globalThis.localStorage?.removeItem(KEYS[k]);
    else globalThis.localStorage?.setItem(KEYS[k], v);
    return;
  }
  if (v === null) await SecureStore.deleteItemAsync(KEYS[k]);
  else await SecureStore.setItemAsync(KEYS[k], v);
};

// 디바이스 ID 중복 생성을 막는 첫 읽기 공유
let loading: Promise<Record<Key, string | null>> | null = null;
const load = () => (loading ??= (async () => ({ access: await get('access'), refresh: await get('refresh'), device: await get('device'), onboarded: await get('onboarded'), disclaimed: await get('disclaimed'), theme: await get('theme') }))());

const randomId = () => (globalThis.crypto?.randomUUID?.() ?? Array.from({ length: 32 }, () => Math.floor(Math.random() * 16).toString(16)).join(''));

type SessionChange = 'login' | 'logout' | 'expired';
let version = 0;
const listeners = new Set<(change: SessionChange, version: number) => Promise<void>>();
// SecureStore 쓰기 순서도 메모리의 세션 전환 순서를 따른다.
let writing: Promise<void> = Promise.resolve();
const persist = (access: string | null, refresh: string | null) => {
  const next = writing.catch(() => undefined).then(async () => {
    await set('access', access);
    await set('refresh', refresh);
  });
  writing = next;
  return next;
};

export const tokens = {
  version: () => version,
  assertCurrent(expected: number) { if (expected !== version) throw new CancelledError(); },
  subscribe(listener: (change: SessionChange, version: number) => Promise<void>) {
    listeners.add(listener);
    return () => { listeners.delete(listener); };
  },
  async access() { return (await load()).access; },
  async refresh() { return (await load()).refresh; },
  async save(access: string, refresh: string, expected = version) {
    const c = await load();
    tokens.assertCurrent(expected);
    c.access = access; c.refresh = refresh;
    await persist(access, refresh);
    tokens.assertCurrent(expected);
  },
  async replace(access: string | null, refresh: string | null, change: SessionChange, expected = version) {
    const c = await load();
    tokens.assertCurrent(expected);
    const current = ++version;
    c.access = access; c.refresh = refresh;
    const saved = persist(access, refresh);
    await Promise.all([saved, ...Array.from(listeners, (listener) => listener(change, current))]);
    tokens.assertCurrent(current);
  },
  clear(change: 'logout' | 'expired' = 'logout', expected = version) {
    return tokens.replace(null, null, change, expected);
  },
};

export const onboarding = {
  async done() { return (await load()).onboarded === '1'; },
  async save(done: boolean) {
    const c = await load();
    c.onboarded = done ? '1' : null;
    await set('onboarded', c.onboarded);
  },
};

export const guestDisclaimer = {
  async done() { return (await load()).disclaimed === '1'; },
  async save() {
    const c = await load();
    c.disclaimed = '1';
    await set('disclaimed', '1');
  },
};

export const themePref = {
  async load() { return (await load()).theme; },
  async save(v: string) {
    const c = await load();
    c.theme = v;
    await set('theme', v);
  },
};

// 첫 요청 때 만들어 영구 보관하는 게스트 식별자
export const deviceId = async () => {
  const c = await load();
  if (!c.device) {
    c.device = randomId();
    await set('device', c.device);
  }
  return c.device;
};
