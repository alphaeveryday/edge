import { Platform } from 'react-native';
import PostHog from 'posthog-react-native';
import { create } from 'zustand';
import { initialAdsPreference, setAdsCollection } from '@/lib/ads';
import { useSession } from '@/store/session';

type Props = Record<string, string | number | boolean>;
const key = process.env.EXPO_PUBLIC_POSTHOG_KEY;
const hasPostHog = !!key && process.env.EXPO_PUBLIC_API_MODE === 'http';
let client: PostHog | undefined;
let preferenceChanged = false;

function getClient() {
  if (!hasPostHog) return;
  if (!client) {
    client = new PostHog(key!, {
      host: 'https://us.i.posthog.com',
      captureAppLifecycleEvents: true,
      enableSessionReplay: false,
      persistence: Platform.OS === 'web' ? 'memory' : 'file',
    });
    client.register({ logged_in: useSession.getState().loggedIn });
  }
  return client;
}
// A failed native read resolves to false; do not let PostHog's empty-store fallback re-enable it.
if (Platform.OS === 'web' || initialAdsPreference === true) getClient();
useSession.subscribe((s, p) => {
  if (s.loggedIn !== p.loggedIn) client?.register({ logged_in: s.loggedIn });
});
export const track = (event: string, props?: Props) => {
  if (useAnalyticsOn.getState().on) client?.capture(event, props);
};
export const trackScreen = (name: string) => {
  if (useAnalyticsOn.getState().on) client?.screen(name);
};

export const useAnalyticsOn = create<{ on: boolean; setOn: (on: boolean) => void }>((set) => ({
  on: initialAdsPreference ?? true,
  setOn: (on) => {
    preferenceChanged = true;
    set({ on });
    // Keep runtime toggles immediate; neither restoration nor an ATT dialog delays revocation.
    const update = on ? getClient()?.optIn() : client?.optOut();
    void update?.catch((error) => console.warn('PostHog preference update failed', error));
    // With no PostHog client, the native bridge writes the same existing preference file.
    void setAdsCollection(on, !hasPostHog).catch((error) => console.warn('Ads collection update failed', error));
  },
}));
// Native already restored the preference before React started. Only web needs JS restoration.
if (Platform.OS === 'web') {
  void client?.ready().then(() => {
    if (!preferenceChanged) useAnalyticsOn.setState({ on: !client!.optedOut });
  }).catch((error) => console.warn('Analytics preference restore failed', error));
}
