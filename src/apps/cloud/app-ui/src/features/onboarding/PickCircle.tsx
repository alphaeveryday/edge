import type { ReactNode } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import Svg, { Path } from 'react-native-svg';
import { createStyles, useColors } from '@/theme/theme';
import { fam } from '@/theme/typography';

interface Props {
  size: number;
  label: string;
  on: boolean;
  hot?: boolean;
  onPress: () => void;
  children: ReactNode;
}

// ETF 선택 그리드의 원형 항목
export function PickCircle({ size, label, on, hot, onPress, children }: Props) {
  const styles = useStyles();
  const colors = useColors();
  const check = size > 88 ? 28 : 26;
  return (
    <Pressable onPress={onPress} style={styles.item}>
      <View style={[styles.ring, { width: size, height: size }, on && styles.ringOn]}>
        {children}
        {!on && <View style={[StyleSheet.absoluteFill, styles.dim]} />}
        {hot && (
          <View style={styles.hot}>
            <Text style={styles.hotText}>🔥 HOT</Text>
          </View>
        )}
        {on && (
          <View style={[styles.check, { width: check, height: check }]}>
            <Svg width={14} height={14} viewBox="0 0 16 16">
              <Path d="M3.5 8.5l3 3 6-7" stroke={colors.onPrimary} strokeWidth={2.4} fill="none" strokeLinecap="round" strokeLinejoin="round" />
            </Svg>
          </View>
        )}
      </View>
      <Text style={[styles.label, { fontFamily: on ? fam.extrabold : fam.semibold, color: on ? colors.text : colors.textSub }]}>{label}</Text>
    </Pressable>
  );
}

const useStyles = createStyles((colors) => ({
  item: { alignItems: 'center', gap: 10 },
  ring: { borderRadius: 999, alignItems: 'center', justifyContent: 'center' },
  ringOn: { shadowColor: colors.primary, shadowOpacity: 1, shadowRadius: 0, shadowOffset: { width: 0, height: 0 }, borderWidth: 3, borderColor: colors.primary },
  dim: { borderRadius: 999, backgroundColor: colors.bg, opacity: 0.35 },
  hot: { position: 'absolute', top: -10, alignSelf: 'center', backgroundColor: colors.up, borderRadius: 999, paddingVertical: 3, paddingHorizontal: 8, shadowColor: colors.up, shadowOpacity: 0.3, shadowRadius: 5, shadowOffset: { width: 0, height: 4 } },
  hotText: { fontFamily: fam.extrabold, fontSize: 11, color: colors.onPrimary },
  check: { position: 'absolute', right: -2, bottom: -2, borderRadius: 999, backgroundColor: colors.primary, borderWidth: 2.5, borderColor: colors.bg, alignItems: 'center', justifyContent: 'center' },
  label: { fontSize: 14, letterSpacing: -0.3, textAlign: 'center', lineHeight: 18 },
}));
