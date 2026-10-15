import { View } from 'react-native';
import { Icon } from '@/components/ui/Icon';
import { createStyles, useColors } from '@/theme/theme';

// 관심 그룹·종목 선택의 체크 원
export function CheckCircle({ on }: { on: boolean }) {
  const styles = useStyles();
  const colors = useColors();
  return (
    <View style={[styles.ck, on && styles.ckOn]}>
      <Icon name="check" color={on ? colors.onPrimary : colors.line} size={12} strokeWidth={2} />
    </View>
  );
}

const useStyles = createStyles((colors) => ({
  ck: { width: 24, height: 24, borderRadius: 999, borderWidth: 1.6, borderColor: colors.lineStrong, alignItems: 'center', justifyContent: 'center' },
  ckOn: { backgroundColor: colors.primary, borderColor: colors.primary },
}));
