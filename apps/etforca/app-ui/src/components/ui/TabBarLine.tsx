import { Platform, View } from 'react-native';
import { createStyles } from '@/theme/theme';

// Android Material 하단 탭과 본문의 구분선. 하단 탭에 선 설정이 없어 탭 화면 맨 아래에 그림
// 탭 화면은 하단 탭 높이만큼 위에서 끝나 화면 맨 아래가 곧 하단 탭 위쪽 끝
export function TabBarLine() {
  const styles = useStyles();
  if (Platform.OS !== 'android') return null;
  return <View pointerEvents="none" style={styles.line} />;
}

const useStyles = createStyles((colors) => ({
  line: { position: 'absolute', left: 0, right: 0, bottom: 0, height: 1, backgroundColor: colors.line },
}));
