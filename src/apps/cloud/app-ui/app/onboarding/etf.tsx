import { useRouter } from 'expo-router';
import { useMemo, useState } from 'react';
import { ScrollView, StyleSheet, Text, View } from 'react-native';
import { SearchField, SectorIcon } from '@/components/ui';
import { useEtfList } from '@/features/etf/queries';
import { PickCircle } from '@/features/onboarding/PickCircle';
import { PickShell } from '@/features/onboarding/PickShell';
import { useOnboarding } from '@/store/onboarding';
import { colors } from '@/theme/tokens';
import { fam } from '@/theme/typography';

const short = (name: string) => name.replace(/^(TIGER|KODEX|PLUS|HANARO|SOL)\s*/, '');

export default function EtfPick() {
  const router = useRouter();
  const { data } = useEtfList();
  const { themes, etfs, toggleEtf } = useOnboarding();
  const [q, setQ] = useState('');
  const list = useMemo(() => {
    const all = data ?? [];
    const ranked = [...all].sort((a, b) => Number(themes.includes(b.theme)) - Number(themes.includes(a.theme)));
    const k = q.trim();
    return k ? ranked.filter((e) => e.name.includes(k) || e.theme.includes(k)) : ranked;
  }, [data, themes, q]);
  const n = etfs.length;
  return (
    <PickShell
      navTitle=""
      title="지켜볼 ETF를 골라주세요"
      sub={themes.length ? `${themes.slice(0, 2).join(' · ')} ETF를 먼저 보여드려요.` : '전망이 좋은 ETF부터 보여드려요.'}
      cta={`${n}개 선택`}
      ctaDisabled={n === 0}
      onBack={() => router.back()}
      onNext={() => router.push('/login')}
    >
      <View style={styles.search}>
        <SearchField value={q} onChangeText={setQ} placeholder="ETF·테마 검색" />
      </View>
      <ScrollView contentContainerStyle={styles.list} showsVerticalScrollIndicator={false} keyboardShouldPersistTaps="handled">
        <View style={styles.grid}>
          {list.map((e) => (
            <View key={e.code} style={styles.cell}>
              <PickCircle size={84} label={short(e.name)} on={etfs.includes(e.code)} hot={e.hot} onPress={() => toggleEtf(e.code)}>
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
