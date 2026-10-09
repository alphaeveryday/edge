import { View } from 'react-native';
import Svg, { Path } from 'react-native-svg';
import { createStyles, useColors } from '@/theme/theme';

// 관심 그룹·종목 선택의 체크 원
export function CheckCircle({ on }: { on: boolean }) {
  const styles = useStyles();
  const colors = useColors();
  return (
    <View style={[styles.ck, on && styles.ckOn]}>
      <Svg width={12} height={12} viewBox="0 0 12 12"><Path d="M2.5 6.3l2.2 2.2 4.8-5" stroke={on ? colors.onPrimary : colors.line} strokeWidth={2} fill="none" strokeLinecap="round" strokeLinejoin="round" /></Svg>
    </View>
  );
}

const useStyles = createStyles((colors) => ({
  ck: { width: 24, height: 24, borderRadius: 999, borderWidth: 1.6, borderColor: colors.lineStrong, alignItems: 'center', justifyContent: 'center' },
  ckOn: { backgroundColor: colors.primary, borderColor: colors.primary },
}));
