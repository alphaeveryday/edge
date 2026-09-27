import { Pressable, StyleSheet, Text, View } from 'react-native';
import { colors } from '@/theme/tokens';
import { fam } from '@/theme/typography';

export function ToggleRow({ label, sub, on, onToggle, divider }: { label: string; sub?: string; on: boolean; onToggle: () => void; divider?: boolean }) {
  return (
    <View style={[styles.row, divider && styles.divider]}>
      <View style={{ flex: 1, gap: 3 }}>
        <Text style={styles.label}>{label}</Text>
        {!!sub && <Text style={styles.sub}>{sub}</Text>}
      </View>
      <Pressable onPress={onToggle} style={[styles.track, { backgroundColor: on ? colors.primary : '#D5DAE0' }]}>
        <View style={[styles.knob, { left: on ? 22 : 2 }]} />
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 14 },
  divider: { borderBottomWidth: 1, borderBottomColor: colors.surface },
  label: { fontFamily: fam.semibold, fontSize: 15.5, color: colors.text, letterSpacing: -0.3 },
  sub: { fontFamily: fam.regular, fontSize: 12.5, color: colors.textFaint },
  track: { width: 50, height: 30, borderRadius: 999 },
  knob: { position: 'absolute', top: 2, width: 26, height: 26, borderRadius: 999, backgroundColor: colors.white, shadowColor: '#000', shadowOpacity: 0.2, shadowRadius: 2, shadowOffset: { width: 0, height: 1 }, elevation: 2 },
});
