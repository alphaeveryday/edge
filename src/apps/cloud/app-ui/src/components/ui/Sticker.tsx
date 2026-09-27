import { LinearGradient } from 'expo-linear-gradient';
import { StyleSheet, Text, View } from 'react-native';
import { signal as SIG, type Signal } from '@/theme/tokens';
import { fam } from '@/theme/typography';

interface Props {
  signal: Signal;
  size?: number;
  radius?: number;
  showLabel?: boolean;
  label?: string;
}

export function Sticker({ signal, size = 28, radius = 9, showLabel = true, label }: Props) {
  const s = SIG[signal];
  const markSize = s.double ? 9 : 13;
  return (
    <View
      style={[
        styles.box,
        { height: size, minWidth: showLabel ? 44 : size, width: showLabel ? undefined : size, paddingHorizontal: showLabel ? 9 : 0, borderRadius: radius, backgroundColor: s.bg, shadowColor: s.color },
      ]}
    >
      <LinearGradient colors={['rgba(255,255,255,0.8)', 'rgba(255,255,255,0.42)']} start={{ x: 0, y: 0 }} end={{ x: 0.5, y: 1 }} style={[StyleSheet.absoluteFill, { borderRadius: radius }]} />
      <View style={styles.marks}>
        <Text style={[styles.mark, { color: s.color, fontSize: markSize, lineHeight: markSize * (s.double ? 0.9 : 1) }]}>{s.mark}</Text>
        {s.double && <Text style={[styles.mark, { color: s.color, fontSize: markSize, lineHeight: markSize * 0.9 }]}>{s.mark}</Text>}
      </View>
      {showLabel && <Text style={[styles.label, { color: s.labelColor }]}>{label ?? s.label}</Text>}
    </View>
  );
}

const styles = StyleSheet.create({
  box: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 4, overflow: 'hidden',
    borderWidth: 1, borderColor: 'rgba(255,255,255,0.75)',
    shadowOpacity: 0.18, shadowRadius: 10, shadowOffset: { width: 0, height: 4 }, elevation: 2,
  },
  marks: { alignItems: 'center', justifyContent: 'center' },
  mark: { fontFamily: fam.extrabold, textAlign: 'center' },
  label: { fontFamily: fam.extrabold, fontSize: 12.5 },
});
