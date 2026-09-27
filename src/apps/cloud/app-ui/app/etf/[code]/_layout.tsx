import { Link, Slot, useLocalSearchParams, usePathname, useRouter } from 'expo-router';
import { useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import Svg, { Path } from 'react-native-svg';
import { IconButton } from '@/components/ui';
import { Pressable } from 'react-native';
import { useEtf } from '@/features/etf/queries';
import { PickGroupSheet } from '@/features/watch/PickGroupSheet';
import { useMembership } from '@/features/watch/queries';
import { colors } from '@/theme/tokens';
import { fam } from '@/theme/typography';

const TABS = [
  { seg: 'brief', label: 'AI 분석' },
  { seg: 'summary', label: '오늘 움직임' },
  { seg: 'community', label: '커뮤니티' },
  { seg: 'data', label: '종목정보' },
] as const;

export default function EtfLayout() {
  const { code } = useLocalSearchParams<{ code: string }>();
  const path = usePathname();
  const router = useRouter();
  const { top } = useSafeAreaInsets();
  const { data } = useEtf(code);
  const { data: mine } = useMembership(code);
  const [pick, setPick] = useState(false);
  const inWatch = (mine ?? []).length > 0;
  return (
    <View style={[styles.root, { paddingTop: top + 8 }]}>
      <View style={styles.nav}>
        <IconButton icon="back" onPress={() => router.back()} />
        <Text numberOfLines={1} style={styles.name}>{data?.name ?? code}</Text>
        <Pressable onPress={() => setPick(true)} hitSlop={6} style={styles.heart}>
          <Svg width={22} height={22} viewBox="0 0 24 24">
            <Path d="M12 21s-7-4.6-9.3-9.3C.9 8 3 4.5 6.5 4.5c2 0 3.5 1 4.5 2.6 1-1.6 2.5-2.6 4.5-2.6 3.5 0 5.6 3.5 3.8 7.2C19 16.4 12 21 12 21z" fill={inWatch ? colors.up : 'none'} stroke={inWatch ? colors.up : colors.text} strokeWidth={1.8} strokeLinejoin="round" />
          </Svg>
        </Pressable>
      </View>
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
      <PickGroupSheet etf={pick && data ? data : null} onClose={() => setPick(false)} />
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.bg },
  nav: { height: 44, flexDirection: 'row', alignItems: 'center', paddingHorizontal: 8 },
  name: { flex: 1, textAlign: 'center', fontFamily: fam.extrabold, fontSize: 16, color: colors.text, letterSpacing: -0.3 },
  heart: { width: 38, height: 38, alignItems: 'center', justifyContent: 'center' },
  tabs: { flexDirection: 'row', borderBottomWidth: 1, borderColor: colors.line, marginTop: 4 },
  tab: { flex: 1, textAlign: 'center', paddingVertical: 12, color: colors.textFaint, fontSize: 14, fontFamily: fam.semibold },
  tabOn: { color: colors.text, fontFamily: fam.bold },
});
