import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useState } from 'react';
import { Pressable, ScrollView, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { NavBar, PageScroll, SearchField, SectionHead, SectorIcon } from '@/components/ui';
import { EtfRow } from '@/features/etf/EtfRow';
import { useEtfSearch, useRecentEtfs } from '@/features/etf/queries';
import { chgColor, pct } from '@/lib/format';
import { createStyles, useColors } from '@/theme/theme';
import { PAGE_X } from '@/theme/tokens';
import { fam } from '@/theme/typography';

export default function Search() {
  const styles = useStyles();
  const colors = useColors();
  const router = useRouter();
  const { top } = useSafeAreaInsets();
  const [q, setQ] = useState('');
  const searching = q.trim().length > 0;
  const results = useEtfSearch(q);
  const recent = useRecentEtfs();
  // ETF 상세에서 돌아올 때 최근 목록 재조회
  const { refetch } = recent;
  useFocusEffect(useCallback(() => { refetch(); }, [refetch]));
  return (
    <View style={[styles.root, { paddingTop: top + 8 }]}>
      <NavBar title="검색" onBack={() => router.back()} />
      <View style={styles.field}>
        <SearchField value={q} onChangeText={setQ} placeholder="ETF·테마 검색" autoFocus />
      </View>
      <PageScroll keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
        {searching ? (
          <View style={styles.results}>
            {results.data?.map((e) => <EtfRow key={e.code} etf={e} showSub />)}
            {results.data && results.data.length === 0 && <Text style={styles.empty}>검색 결과가 없어요</Text>}
          </View>
        ) : (
          <>
            <View style={{ paddingTop: 24 }}><SectionHead title="최근 본 ETF" /></View>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.recent}>
              {recent.data?.map((e) => (
                <Pressable key={e.code} onPress={() => router.push(`/etf/${e.code}/brief`)} style={({ pressed }) => [styles.chip, pressed && { opacity: 0.6 }]}>
                  <SectorIcon theme={e.theme} bg={e.logoBg} size={22} />
                  <Text style={styles.chipName}>{e.name}</Text>
                  <Text style={[styles.chipChg, { color: chgColor(colors, e.changePct) }]}>{pct(e.changePct)}</Text>
                </Pressable>
              ))}
            </ScrollView>
          </>
        )}
      </PageScroll>
    </View>
  );
}

const useStyles = createStyles((colors) => ({
  root: { flex: 1, backgroundColor: colors.bg },
  field: { marginTop: 12, marginHorizontal: PAGE_X },
  results: { paddingTop: 8, paddingHorizontal: PAGE_X },
  empty: { textAlign: 'center', fontFamily: fam.regular, fontSize: 14, color: colors.textMuted, paddingVertical: 40 },
  recent: { flexDirection: 'row', gap: 8, paddingTop: 12, paddingHorizontal: PAGE_X },
  chip: { flexDirection: 'row', alignItems: 'center', gap: 7, backgroundColor: colors.surface, borderRadius: 999, paddingVertical: 8, paddingLeft: 8, paddingRight: 13 },
  chipName: { fontFamily: fam.semibold, fontSize: 14, color: colors.text },
  chipChg: { fontFamily: fam.semibold, fontSize: 13 },
}));
