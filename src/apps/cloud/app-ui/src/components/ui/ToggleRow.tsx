import { Pressable, Text, View } from 'react-native';
import { createStyles, useColors } from '@/theme/theme';
import { radius } from '@/theme/tokens';
import { type } from '@/theme/typography';

export function ToggleRow({ label, sub, on, onToggle, divider }: { label: string; sub?: string; on: boolean; onToggle: () => void; divider?: boolean }) {
  const styles = useStyles();
  const colors = useColors();
  return (
    <View style={[styles.row, divider && styles.divider]}>
      <View style={{ flex: 1, gap: 3 }}>
        <Text style={styles.label}>{label}</Text>
        {!!sub && <Text style={styles.sub}>{sub}</Text>}
      </View>
      <Pressable onPress={onToggle} style={[styles.track, { backgroundColor: on ? colors.primary : colors.lineStrong }]}>
        <View style={[styles.knob, { left: on ? 22 : 2 }]} />
      </Pressable>
    </View>
  );
}

const useStyles = createStyles((colors) => ({
  row: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 14 },
  divider: { borderBottomWidth: 1, borderBottomColor: colors.surface },
  label: { ...type.listLabel, color: colors.text },
  sub: { ...type.caption, color: colors.textMuted },
  track: { width: 50, height: 30, borderRadius: radius.pill },
  knob: { position: 'absolute', top: 2, width: 26, height: 26, borderRadius: radius.pill, backgroundColor: colors.bg, shadowColor: '#000', shadowOpacity: 0.2, shadowRadius: 2, shadowOffset: { width: 0, height: 1 }, elevation: 2 },
}));
