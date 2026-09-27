import { Link, Slot, useLocalSearchParams, usePathname } from 'expo-router';
import { StyleSheet, Text, View } from 'react-native';
import { useEtf } from '@/features/etf/queries';
import { colors, space } from '@/theme/tokens';

const TABS = [
  { seg: 'brief', label: 'AI 분석' },
  { seg: 'summary', label: '오늘 움직임' },
  { seg: 'community', label: '커뮤니티' },
  { seg: 'data', label: '종목정보' },
] as const;

export default function EtfLayout() {
  const { code } = useLocalSearchParams<{ code: string }>();
  const path = usePathname();
  const { data } = useEtf(code);
  return (
    <View style={styles.root}>
      <Text style={styles.name}>{data?.name ?? code}</Text>
      <View style={styles.tabs}>
        {TABS.map((t) => {
          const on = path.endsWith(`/${t.seg}`);
          return (
            <Link key={t.seg} href={`/etf/${code}/${t.seg}`} replace style={[styles.tab, on && styles.tabOn]}>
              {t.label}
            </Link>
          );
        })}
      </View>
      <Slot />
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.bg, paddingTop: 56 },
  name: { fontSize: 17, fontWeight: '700', color: colors.text, paddingHorizontal: space.xl },
  tabs: { flexDirection: 'row', borderBottomWidth: 1, borderColor: colors.line, marginTop: space.md },
  tab: { flex: 1, textAlign: 'center', paddingVertical: space.md, color: colors.textFaint, fontSize: 14 },
  tabOn: { color: colors.text, fontWeight: '700' },
});
