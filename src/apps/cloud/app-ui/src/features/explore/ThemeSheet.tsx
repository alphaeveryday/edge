import { useRouter } from 'expo-router';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { BottomSheet, LinkRow, SectorIcon, SheetHead, Sticker } from '@/components/ui';
import { chgColor, pct } from '@/lib/format';
import { colors } from '@/theme/tokens';
import { fam } from '@/theme/typography';
import { useThemeSheet } from './queries';

export function ThemeSheet({ theme, onClose }: { theme: string | null; onClose: () => void }) {
  const router = useRouter();
  const { data } = useThemeSheet(theme);
  const go = (path: string) => { onClose(); router.push(path as never); };
  return (
    <BottomSheet open={!!theme} onClose={onClose}>
      <SheetHead title={data?.title ?? ''} sub={data?.why} onClose={onClose} />
      <ScrollView style={{ marginTop: 6 }} showsVerticalScrollIndicator={false}>
        {data?.rows.map(({ etf, tag }) => (
          <Pressable key={etf.code} onPress={() => go(`/etf/${etf.code}/brief`)} style={({ pressed }) => [styles.row, pressed && { opacity: 0.6 }]}>
            <SectorIcon theme={etf.theme} bg={etf.logoBg} size={36} />
            <View style={styles.mid}>
              <Text numberOfLines={1} style={styles.name}>{etf.name}</Text>
              <View style={styles.sub}>
                <Sticker signal={etf.signal} size={22} radius={7} />
                {!!tag && <Text style={styles.tag}>{tag}</Text>}
              </View>
            </View>
            <Text style={[styles.chg, { color: chgColor(etf.changePct) }]}>{pct(etf.changePct)}</Text>
          </Pressable>
        ))}
        <View style={{ marginTop: 12, marginBottom: 8 }}>
          <LinkRow variant="card" muted label="전망 좋은 순으로 전체 보기" onPress={() => go(`/themes/compare?theme=${encodeURIComponent(theme ?? '')}`)} />
        </View>
      </ScrollView>
    </BottomSheet>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 14, borderBottomWidth: 1, borderBottomColor: colors.surface },
  mid: { flex: 1, gap: 5 },
  name: { fontFamily: fam.bold, fontSize: 15, color: colors.text },
  sub: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  tag: { fontFamily: fam.bold, fontSize: 11, color: colors.textSub, backgroundColor: colors.surface, borderRadius: 6, paddingVertical: 3, paddingHorizontal: 7, overflow: 'hidden' },
  chg: { fontFamily: fam.monoExtraBold, fontSize: 15 },
});
