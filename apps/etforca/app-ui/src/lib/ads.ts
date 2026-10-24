import { Platform } from 'react-native';

// 설치 광고 측정: Firebase 애널리틱스 first_open 과 Meta 앱 이벤트
// 웹 빌드는 네이티브 모듈이 없어 제외
const native = Platform.OS !== 'web';

// iOS 추적 허용 결과를 Meta SDK 에 전달한 뒤 초기화
export async function startAds() {
  if (!native) return;
  const { requestTrackingPermissionsAsync } = await import('expo-tracking-transparency');
  const { Settings } = await import('react-native-fbsdk-next');
  const { status } = await requestTrackingPermissionsAsync();
  if (Platform.OS === 'ios') await Settings.setAdvertiserTrackingEnabled(status === 'granted');
  Settings.initializeSDK();
}

// 이용 통계 토글과 연동, 두 SDK 모두 설정값을 저장소에 보관
export async function setAdsCollection(on: boolean) {
  if (!native) return;
  const { getAnalytics, setAnalyticsCollectionEnabled } = await import('@react-native-firebase/analytics');
  const { Settings } = await import('react-native-fbsdk-next');
  await setAnalyticsCollectionEnabled(getAnalytics(), on);
  Settings.setAutoLogAppEventsEnabled(on);
  Settings.setAdvertiserIDCollectionEnabled(on);
}
