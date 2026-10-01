import { StyleSheet, Text, View } from 'react-native';
import { colors, radius } from '@/theme/tokens';
import { fam } from '@/theme/typography';

export function Avatar({ label, bg = colors.primary, size = 40 }: { label: string; bg?: string; size?: number }) {
  return (
    <View style={[styles.root, { width: size, height: size, backgroundColor: bg }]}>
      <Text style={[styles.ch, { fontSize: Math.round(size * 0.38) }]}>{label.slice(0, 1)}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { borderRadius: radius.pill, alignItems: 'center', justifyContent: 'center' },
  ch: { fontFamily: fam.extrabold, color: colors.white },
});
