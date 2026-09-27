import { useRouter } from 'expo-router';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import type { EtfSummary } from '@/api';
import { RowQuote, SectorIcon, Sticker } from '@/components/ui';
import { colors } from '@/theme/tokens';
import { fam } from '@/theme/typography';

interface Props {
  etf: EtfSummary;
  showSub?: boolean;
  divider?: boolean;
  onPress?: () => void;
}

export function EtfRow({ etf, showSub, divider = true, onPress }: Props) {
  const router = useRouter();
  return (
    <Pressable onPress={onPress ?? (() => router.push(`/etf/${etf.code}/brief`))} style={({ pressed }) => [styles.row, divider && styles.divider, pressed && { opacity: 0.6 }]}>
      <SectorIcon theme={etf.theme} bg={etf.logoBg} size={36} />
      <View style={styles.mid}>
        <Text numberOfLines={1} style={styles.name}>{etf.name}</Text>
        {showSub ? <Text numberOfLines={1} style={styles.sub}>{etf.sub}</Text> : <RowQuote price={etf.price} changePct={etf.changePct} />}
      </View>
      <Sticker signal={etf.signal} />
    </Pressable>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: 11, paddingVertical: 14 },
  divider: { borderBottomWidth: 1, borderBottomColor: colors.surface },
  mid: { flex: 1, gap: 4 },
  name: { fontFamily: fam.bold, fontSize: 15, color: colors.text, letterSpacing: -0.3, lineHeight: 20 },
  sub: { fontFamily: fam.regular, fontSize: 12.5, color: colors.textMuted },
});
