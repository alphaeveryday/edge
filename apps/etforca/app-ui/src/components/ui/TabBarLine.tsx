import { Platform, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { createStyles } from '@/theme/theme';

// Android Material 하단 탭과 본문의 구분선. 하단 탭에 선 설정이 없어 탭 화면이 하단 안전영역 위에 그림
export function TabBarLine() {
  const styles = useStyles();
  const { bottom } = useSafeAreaInsets();
  if (Platform.OS !== 'android') return null;
  return <View pointerEvents="none" style={[styles.line, { bottom }]} />;
}

const useStyles = createStyles((colors) => ({
  line: { position: 'absolute', left: 0, right: 0, height: 1, backgroundColor: colors.line },
}));
