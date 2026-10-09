import { Text, View } from 'react-native';
import { createStyles, useColors } from '@/theme/theme';
import { radius } from '@/theme/tokens';
import { fam } from '@/theme/typography';

export function Avatar({ label, bg, size = 40 }: { label: string; bg?: string; size?: number }) {
  const styles = useStyles();
  const colors = useColors();
  return (
    <View style={[styles.root, { width: size, height: size, backgroundColor: bg ?? colors.primary }]}>
      <Text style={[styles.ch, { fontSize: Math.round(size * 0.38) }]}>{label.slice(0, 1)}</Text>
    </View>
  );
}

const useStyles = createStyles((colors) => ({
  root: { borderRadius: radius.pill, alignItems: 'center', justifyContent: 'center' },
  ch: { fontFamily: fam.extrabold, color: colors.onPrimary },
}));
