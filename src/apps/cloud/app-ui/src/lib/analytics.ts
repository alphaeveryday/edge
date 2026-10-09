import { Platform } from 'react-native';
import PostHog from 'posthog-react-native';
import { useSession } from '@/store/session';

type Props = Record<string, string | number | boolean>;

// 토큰이 있는 실서버 모드에서만 수집
// 웹 검증용 메모리 저장
const key = process.env.EXPO_PUBLIC_POSTHOG_KEY;
const client = key && process.env.EXPO_PUBLIC_API_MODE === 'http'
  ? new PostHog(key, {
      host: 'https://us.i.posthog.com',
      captureAppLifecycleEvents: true,
      enableSessionReplay: false,
      persistence: Platform.OS === 'web' ? 'memory' : 'file',
    })
  : null;

// 모든 이벤트의 로그인 여부
if (client) {
  client.register({ logged_in: useSession.getState().loggedIn });
  useSession.subscribe((s, p) => {
    if (s.loggedIn !== p.loggedIn) client.register({ logged_in: s.loggedIn });
  });
}

export const track = (event: string, props?: Props) => { client?.capture(event, props); };
export const trackScreen = (name: string) => { client?.screen(name); };
