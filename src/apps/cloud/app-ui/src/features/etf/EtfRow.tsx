import { useRouter } from 'expo-router';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import type { EtfSummary } from '@/api';
import { RowQuote, SectorIcon, Sticker } from '@/components/ui';
import { colors } from '@/theme/tokens';
import { fam } from '@/theme/typography';

export function EtfRow({ etf }: { etf: EtfSummary }) {
  const router = useRouter();
  return (
    <Pressable onPress={() => router.push(`/etf/${etf.code}/brief`)} style={({ pressed }) => [styles.row, pressed && { opacity: 0.6 }]}>
      <SectorIcon theme={etf.theme} bg={etf.logoBg} size={36} />
      <View style={styles.mid}>
        <Text numberOfLines={1} style={styles.name}>{etf.name}</Text>
        <RowQuote price={etf.price} changePct={etf.changePct} />
      </View>
      <Sticker signal={etf.signal} />
    </Pressable>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: 11, paddingVertical: 14, borderBottomWidth: 1, borderBottomColor: colors.surface },
  mid: { flex: 1, gap: 5 },
  name: { fontFamily: fam.bold, fontSize: 15, color: colors.text, letterSpacing: -0.3, lineHeight: 20 },
});
