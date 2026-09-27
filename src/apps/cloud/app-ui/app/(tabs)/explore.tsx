import { useRouter } from 'expo-router';
import { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { TOP_BAR_H, TopBar } from '@/components/TopBar';
import { PageTitle, SectionHead, SectorIcon, Sticker } from '@/components/ui';
import { useRank } from '@/features/explore/queries';
import { ThemeSheet } from '@/features/explore/ThemeSheet';
import { colors, PAGE_X } from '@/theme/tokens';
import { Loading } from '@/components/state';
import { fam } from '@/theme/typography';

export default function Explore() {
  const router = useRouter();
  const { top } = useSafeAreaInsets();
  const q = useRank();
  const data = q.data;
  const [theme, setTheme] = useState<string | null>(null);
  return (
    <View style={styles.root}>
      <TopBar />
      <ScrollView contentContainerStyle={{ paddingTop: top + TOP_BAR_H, paddingBottom: 28 }} showsVerticalScrollIndicator={false}>
        <PageTitle title="탐색" meta="오늘 08:30 기준" />
        <View style={{ paddingTop: 22 }}>
          <SectionHead title="AI가 보는 오늘 순위" actionLabel="테마" onAction={() => router.push('/themes')} />
        </View>
        <Text style={styles.lead}>재료가 확인된 ETF부터 위에 있어요.</Text>
        {q.isPending && <Loading rows={5} />}
        <View style={{ paddingTop: 4, paddingHorizontal: PAGE_X }}>
          {data?.map((r) => {
            const top3 = r.rank <= 3;
            return (
              <Pressable key={r.etf.code} onPress={() => router.push(`/etf/${r.etf.code}/brief`)} style={({ pressed }) => [styles.row, pressed && { opacity: 0.6 }]}>
                <View style={styles.rowHead}>
                  <Text style={[styles.rank, top3 ? styles.rankTop : styles.rankPlain]}>{top3 ? `${r.rank}위` : String(r.rank)}</Text>
                  <Pressable onPress={() => setTheme(r.etf.theme)} hitSlop={6} style={styles.themeBtn}>
                    <SectorIcon theme={r.etf.theme} bg={r.etf.logoBg} size={20} />
                    <Text numberOfLines={1} style={styles.name}>{r.etf.name}</Text>
                  </Pressable>
                  <View style={{ flex: 1 }} />
                  <Sticker signal={r.etf.signal} size={24} radius={8} />
                </View>
                <Text numberOfLines={1} style={styles.title}>{r.title}</Text>
                <View style={styles.chips}>
                  {r.chips.map((c) => <Text key={c} style={styles.chip}>{c}</Text>)}
                </View>
              </Pressable>
            );
          })}
        </View>
      </ScrollView>
      <ThemeSheet theme={theme} onClose={() => setTheme(null)} />
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.white },
  lead: { fontFamily: fam.regular, fontSize: 13, color: colors.textMuted, paddingTop: 6, paddingHorizontal: PAGE_X },
  row: { gap: 13, paddingVertical: 24, borderBottomWidth: 1, borderBottomColor: colors.line },
  rowHead: { flexDirection: 'row', alignItems: 'center', gap: 7 },
  rank: { fontFamily: fam.monoExtraBold, fontSize: 12, textAlign: 'center', overflow: 'hidden' },
  rankTop: { color: colors.white, backgroundColor: colors.text, borderRadius: 7, paddingVertical: 3, paddingHorizontal: 7 },
  rankPlain: { color: colors.textFaint, width: 20 },
  themeBtn: { flexDirection: 'row', alignItems: 'center', gap: 7, flexShrink: 1 },
  name: { fontFamily: fam.bold, fontSize: 12, color: colors.textFaint, flexShrink: 1 },
  title: { fontFamily: fam.extrabold, fontSize: 18, lineHeight: 25, letterSpacing: -0.5, color: colors.text },
  chips: { flexDirection: 'row', gap: 6 },
  chip: { fontFamily: fam.bold, fontSize: 12, color: colors.textSub, backgroundColor: colors.card, borderRadius: 7, paddingVertical: 5, paddingHorizontal: 9, overflow: 'hidden' },
});
