import { useRouter } from 'expo-router';
import { useMemo, useState } from 'react';
import { ScrollView, StyleSheet, Text, View } from 'react-native';
import { SearchField, SectorIcon } from '@/components/ui';
import { useEtfList, useThemes } from '@/features/etf/queries';
import { PickCircle } from '@/features/onboarding/PickCircle';
import { PickShell } from '@/features/onboarding/PickShell';
import { api } from '@/api';
import { useOnboarding } from '@/store/onboarding';
import { useSession } from '@/store/session';
import { colors } from '@/theme/tokens';
import { fam } from '@/theme/typography';


export default function EtfPick() {
  const router = useRouter();
  const { data } = useEtfList();
  const { data: themeList } = useThemes();
  const { themes, etfs, toggleEtf } = useOnboarding();
  // ETF 테마 표기에 맞춘 고른 테마의 라벨
  const picked = useMemo(() => (themeList ?? []).filter((t) => themes.includes(t.key)).map((t) => t.label), [themeList, themes]);
  const finishOnboarding = useSession((s) => s.finishOnboarding);
  const [q, setQ] = useState('');
  const list = useMemo(() => {
    const all = data ?? [];
    const ranked = [...all].sort((a, b) => Number(picked.includes(b.theme)) - Number(picked.includes(a.theme)));
    const k = q.trim();
    return k ? ranked.filter((e) => e.name.includes(k) || e.theme.includes(k)) : ranked;
  }, [data, picked, q]);
  const n = etfs.length;
  const done = async () => {
    await api.onboarding.complete({ themes, etfs });
    finishOnboarding();
    router.replace('/(tabs)/home');
  };
  return (
    <PickShell
      navTitle=""
      title="지켜볼 ETF를 골라주세요"
      sub={picked.length ? `${picked.slice(0, 2).join(' · ')} ETF를 먼저 보여드려요.` : '전망이 좋은 ETF부터 보여드려요.'}
      cta={`${n}개 선택`}
      ctaDisabled={n === 0}
      onBack={() => router.back()}
      onNext={done}
    >
      <View style={styles.search}>
        <SearchField value={q} onChangeText={setQ} placeholder="ETF·테마 검색" />
      </View>
      <ScrollView contentContainerStyle={styles.list} showsVerticalScrollIndicator={false} keyboardShouldPersistTaps="handled">
        <View style={styles.grid}>
          {list.map((e) => (
            <View key={e.code} style={styles.cell}>
              <PickCircle size={84} label={e.name} on={etfs.includes(e.code)} hot={e.hot} onPress={() => toggleEtf(e.code)}>
                <SectorIcon theme={e.theme} bg={e.logoBg} size={78} />
              </PickCircle>
            </View>
          ))}
        </View>
        {list.length === 0 && <Text style={styles.empty}>찾는 ETF가 없어요</Text>}
      </ScrollView>
    </PickShell>
  );
}

const styles = StyleSheet.create({
  search: { paddingTop: 12, paddingHorizontal: 20 },
  list: { paddingTop: 22, paddingHorizontal: 16, paddingBottom: 24 },
  grid: { flexDirection: 'row', flexWrap: 'wrap', rowGap: 22 },
  cell: { width: '33.33%', alignItems: 'center' },
  empty: { textAlign: 'center', fontFamily: fam.regular, fontSize: 14, color: colors.textFaint, paddingVertical: 40 },
});
