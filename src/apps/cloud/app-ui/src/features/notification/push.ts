import Constants from 'expo-constants';
import * as Device from 'expo-device';
import * as Notifications from 'expo-notifications';
import { router } from 'expo-router';
import { useEffect, useRef } from 'react';
import { Platform } from 'react-native';
import { api } from '@/api';
import { useSession } from '@/store/session';

// 앱을 보고 있을 때도 배너 표시
Notifications.setNotificationHandler({
  handleNotification: async () => ({ shouldShowBanner: true, shouldShowList: true, shouldPlaySound: true, shouldSetBadge: false }),
});

const enabled = process.env.EXPO_PUBLIC_API_MODE === 'http';

// ask 가 거짓이면 권한이 이미 있을 때만 등록
// 실패는 알림함이 기록이라 조용히 무시
export async function registerPush(ask: boolean) {
  if (!enabled || !Device.isDevice) return;
  try {
    let { granted, canAskAgain } = await Notifications.getPermissionsAsync();
    if (!granted && ask && canAskAgain) granted = (await Notifications.requestPermissionsAsync()).granted;
    if (!granted) return;
    if (Platform.OS === 'android') {
      await Notifications.setNotificationChannelAsync('default', { name: '기본', importance: Notifications.AndroidImportance.DEFAULT });
    }
    const projectId = Constants.expoConfig?.extra?.eas?.projectId as string | undefined;
    const { data } = await Notifications.getExpoPushTokenAsync({ projectId });
    await api.notification.pushToken(data);
  } catch (e) {
    console.warn('push register failed', e);
  }
}

// 로그인·로그아웃마다 재등록으로 서버의 회원 연결 전환
// 알림 탭은 글 화면으로 이동, 같은 응답 중복 처리 없음
export function usePush() {
  const restored = useSession((s) => s.restored);
  const loggedIn = useSession((s) => s.loggedIn);
  const response = Notifications.useLastNotificationResponse();
  const handled = useRef<string | null>(null);
  useEffect(() => { if (restored) registerPush(false); }, [restored, loggedIn]);
  useEffect(() => {
    if (!restored || !response) return;
    const id = response.notification.request.identifier;
    if (handled.current === id) return;
    handled.current = id;
    const postId = response.notification.request.content.data?.postId;
    if (typeof postId === 'string') router.push(`/post/${postId}`);
  }, [restored, response]);
}
