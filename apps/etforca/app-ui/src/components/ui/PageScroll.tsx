import type { Ref } from 'react';
import { ScrollView, type ScrollViewProps, StyleSheet } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

// 끝 여백에 시스템 하단 영역을 더한 탭 밖 세로 스크롤
export function PageScroll({ contentContainerStyle, ref, ...rest }: ScrollViewProps & { ref?: Ref<ScrollView> }) {
  const { bottom } = useSafeAreaInsets();
  const base = StyleSheet.flatten(contentContainerStyle)?.paddingBottom;
  const pad = (typeof base === 'number' ? base : 0) + bottom;
  return <ScrollView ref={ref} {...rest} contentContainerStyle={[contentContainerStyle, { paddingBottom: pad }]} />;
}
