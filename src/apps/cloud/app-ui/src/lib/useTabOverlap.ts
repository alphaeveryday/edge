import { Platform } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

// 화면 위에 겹쳐 그려지는 하단 탭바의 높이
// iOS 리퀴드 글라스 탭바만 탭 화면의 하단 inset 에 포함
export function useTabOverlap() {
  const { bottom } = useSafeAreaInsets();
  return Platform.OS === 'ios' ? bottom : 0;
}
