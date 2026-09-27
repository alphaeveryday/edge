import { useRouter } from 'expo-router';
import { ScrollView, StyleSheet, Text, View } from 'react-native';
import { SectorIcon } from '@/components/ui';
import { useThemes } from '@/features/etf/queries';
import { PickCircle } from '@/features/onboarding/PickCircle';
import { PickShell } from '@/features/onboarding/PickShell';
import { useOnboarding } from '@/store/onboarding';
import { colors } from '@/theme/tokens';
import { fam } from '@/theme/typography';

const GROUPS = [
  { key: 'industry', title: '산업', sub: '무엇이 성장하나' },
  { key: 'asset', title: '자산', sub: '어디에 돈을 두나' },
] as const;

export default function ThemePick() {
  const router = useRouter();
  const { data } = useThemes();
  const { themes, toggleTheme } = useOnboarding();
  const n = themes.length;
  return (
    <PickShell
      title="관심 있는 테마를 골라주세요"
      sub="고른 테마의 ETF를 먼저 보여드려요."
      cta={n ? `${n}개 테마로 계속` : '테마를 골라주세요'}
      ctaDisabled={n === 0}
      onBack={() => router.back()}
      onNext={() => router.push('/onboarding/etf')}
    >
      <ScrollView contentContainerStyle={styles.list} showsVerticalScrollIndicator={false}>
        {GROUPS.map((g) => (
          <View key={g.key} style={{ gap: 16 }}>
            <View style={styles.groupHead}>
              <Text style={styles.groupTitle}>{g.title}</Text>
              <Text style={styles.groupSub}>{g.sub}</Text>
            </View>
            <View style={styles.grid}>
              {data?.filter((t) => t.group === g.key).map((t) => (
                <View key={t.key} style={styles.cell}>
                  <PickCircle size={92} label={t.label} on={themes.includes(t.key)} hot={t.hot} onPress={() => toggleTheme(t.key)}>
                    <SectorIcon theme={t.label} bg={t.bg} size={86} />
                  </PickCircle>
                </View>
              ))}
            </View>
          </View>
        ))}
      </ScrollView>
    </PickShell>
  );
}

const styles = StyleSheet.create({
  list: { paddingTop: 22, paddingHorizontal: 16, paddingBottom: 24, gap: 30 },
  groupHead: { flexDirection: 'row', alignItems: 'baseline', gap: 8, paddingHorizontal: 4 },
  groupTitle: { fontFamily: fam.extrabold, fontSize: 15, color: colors.text, letterSpacing: -0.3 },
  groupSub: { fontFamily: fam.regular, fontSize: 13, color: colors.textFaint },
  grid: { flexDirection: 'row', flexWrap: 'wrap', rowGap: 26, paddingTop: 8 },
  cell: { width: '33.33%', alignItems: 'center' },
});
