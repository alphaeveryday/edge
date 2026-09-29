import { Platform } from 'react-native';
import * as SecureStore from 'expo-secure-store';

// 토큰·디바이스 ID·온보딩 완료 보관. 네이티브는 키체인, 웹 미리보기는 localStorage
const KEYS = { access: 'etforca.access', refresh: 'etforca.refresh', device: 'etforca.device', onboarded: 'etforca.onboarded' } as const;
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

// 동시 첫 호출이 한 번의 읽기를 공유. 따로 읽으면 디바이스 ID 가 둘 생긴다
let loading: Promise<Record<Key, string | null>> | null = null;
const load = () => (loading ??= (async () => ({ access: await get('access'), refresh: await get('refresh'), device: await get('device'), onboarded: await get('onboarded') }))());

const randomId = () => (globalThis.crypto?.randomUUID?.() ?? Array.from({ length: 32 }, () => Math.floor(Math.random() * 16).toString(16)).join(''));

export const tokens = {
  async access() { return (await load()).access; },
  async refresh() { return (await load()).refresh; },
  async save(access: string, refresh: string) {
    const c = await load();
    c.access = access; c.refresh = refresh;
    await set('access', access);
    await set('refresh', refresh);
  },
  async clear() {
    const c = await load();
    c.access = null; c.refresh = null;
    await set('access', null);
    await set('refresh', null);
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

// 게스트 식별자. 처음 요청할 때 만들어 영구 보관
export const deviceId = async () => {
  const c = await load();
  if (!c.device) {
    c.device = randomId();
    await set('device', c.device);
  }
  return c.device;
};
