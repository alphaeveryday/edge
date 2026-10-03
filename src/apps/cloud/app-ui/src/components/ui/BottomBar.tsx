import type { ReactNode } from 'react';
import { type StyleProp, View, type ViewStyle } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

// 시스템 하단 영역 위 12의 하단 고정 영역 아래 여백
export function useBottomGap() {
  const { bottom } = useSafeAreaInsets();
  return Math.max(bottom, 16) + 12;
}

export function BottomBar({ style, children }: { style?: StyleProp<ViewStyle>; children: ReactNode }) {
  const gap = useBottomGap();
  return <View style={[style, { paddingBottom: gap }]}>{children}</View>;
}
