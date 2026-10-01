import { Pressable, StyleSheet, Text, View } from 'react-native';
import { colors, radius } from '@/theme/tokens';
import { fam, type } from '@/theme/typography';

export function TabItem({ label, on, dot, grow = true, onPress }: { label: string; on: boolean; dot?: boolean; grow?: boolean; onPress?: () => void }) {
  return (
    <Pressable onPress={onPress} style={[styles.tab, grow ? styles.grow : styles.fit, on && styles.on]}>
      <Text numberOfLines={1} style={[styles.label, { color: on ? colors.text : colors.textFaint, fontFamily: on ? fam.extrabold : fam.semibold }]}>{label}</Text>
      {dot && <View style={styles.dot} />}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  tab: { alignItems: 'center', paddingTop: 10, paddingBottom: 11, borderBottomWidth: 2, borderBottomColor: 'transparent' },
  grow: { flex: 1 },
  fit: { paddingHorizontal: 12 },
  on: { borderBottomColor: colors.text },
  label: type.tab,
  dot: { position: 'absolute', top: 8, right: 4, width: 5, height: 5, borderRadius: radius.pill, backgroundColor: colors.up },
});
