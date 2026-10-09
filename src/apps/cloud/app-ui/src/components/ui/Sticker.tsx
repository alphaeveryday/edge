import { LinearGradient } from 'expo-linear-gradient';
import { StyleSheet, Text, View } from 'react-native';
import { createStyles, useColors, useSignal } from '@/theme/theme';
import { type Signal } from '@/theme/tokens';
import { fam, type } from '@/theme/typography';

interface Props {
  signal: Signal;
  size?: number;
  radius?: number;
  showLabel?: boolean;
  label?: string;
  minWidth?: number;
}

export function Sticker({ signal, size = 28, radius = 9, showLabel = true, label, minWidth = 44 }: Props) {
  const styles = useStyles();
  const SIG = useSignal();
  const colors = useColors();
  const s = SIG[signal];
  // 스티커 크기 28 기준의 기호 크기 비례
  const markSize = (size * (s.double ? 9 : 13)) / 28;
  return (
    <View
      style={[
        styles.box,
        { height: size, minWidth: showLabel ? minWidth : size, width: showLabel ? undefined : size, paddingHorizontal: showLabel ? 9 : 0, borderRadius: radius, backgroundColor: s.bg, shadowColor: s.color },
      ]}
    >
      <LinearGradient colors={colors.gloss} start={{ x: 0, y: 0 }} end={{ x: 0.5, y: 1 }} style={[StyleSheet.absoluteFill, { borderRadius: radius }]} />
      <View style={styles.marks}>
        <Text style={[styles.mark, { color: s.color, fontSize: markSize, lineHeight: markSize * (s.double ? 0.9 : 1) }]}>{s.mark}</Text>
        {s.double && <Text style={[styles.mark, { color: s.color, fontSize: markSize, lineHeight: markSize * 0.9 }]}>{s.mark}</Text>}
      </View>
      {showLabel && <Text style={[styles.label, { color: s.labelColor }]}>{label ?? s.label}</Text>}
    </View>
  );
}

const useStyles = createStyles((colors) => ({
  box: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 4, overflow: 'hidden',
    borderWidth: 1, borderColor: colors.glossLine,
    shadowOpacity: 0.18, shadowRadius: 10, shadowOffset: { width: 0, height: 4 }, elevation: 2,
  },
  marks: { alignItems: 'center', justifyContent: 'center' },
  mark: { fontFamily: fam.extrabold, textAlign: 'center' },
  label: type.sticker,
}));
