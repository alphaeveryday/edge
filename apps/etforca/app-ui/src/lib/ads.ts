import { Platform } from 'react-native';
import { requireNativeModule } from 'expo-modules-core';

type AdsConsent = {
  getInitialPreference(): boolean | null;
  setCollectionEnabled(on: boolean, persist: boolean): Promise<void>;
  setTrackingAllowed(allowed: boolean): Promise<void>;
};
const native = Platform.OS !== 'web';
const consent = native ? requireNativeModule<AdsConsent>('AdsConsent') : undefined;
export const initialAdsPreference = consent?.getInitialPreference() ?? null;
let updates: Promise<void> = Promise.resolve();
let permission: Promise<void> | undefined;

// Collection is restored in native startup. JS only requests/updates ATT after the splash.
export function startAds() {
  if (Platform.OS !== 'ios') return Promise.resolve();
  return permission ??= requestTracking().catch((error) => { permission = undefined; throw error; });
}
async function requestTracking() {
  const { requestTrackingPermissionsAsync } = await import('expo-tracking-transparency');
  const { status } = await requestTrackingPermissionsAsync();
  await consent?.setTrackingAllowed(status === 'granted');
}

export function setAdsCollection(on: boolean, persist = false) {
  updates = updates.catch(() => {}).then(async () => {
    await consent?.setCollectionEnabled(on, persist);
  });
  return updates;
}
