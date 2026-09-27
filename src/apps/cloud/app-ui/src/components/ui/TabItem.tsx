import { Pressable, StyleSheet, Text, View } from 'react-native';
import { colors } from '@/theme/tokens';
import { fam } from '@/theme/typography';

export function TabItem({ label, on, dot, onPress }: { label: string; on: boolean; dot?: boolean; onPress?: () => void }) {
  return (
    <Pressable onPress={onPress} style={[styles.tab, on && styles.on]}>
      <Text style={[styles.label, { color: on ? colors.text : colors.textFaint, fontFamily: on ? fam.extrabold : fam.semibold }]}>{label}</Text>
      {dot && <View style={styles.dot} />}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  tab: { flex: 1, alignItems: 'center', paddingTop: 10, paddingBottom: 11, borderBottomWidth: 2, borderBottomColor: 'transparent' },
  on: { borderBottomColor: colors.text },
  label: { fontSize: 14 },
  dot: { position: 'absolute', top: 8, right: 4, width: 5, height: 5, borderRadius: 999, backgroundColor: colors.up },
});
