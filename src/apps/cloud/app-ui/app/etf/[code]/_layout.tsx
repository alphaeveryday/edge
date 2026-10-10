import { Slot, useLocalSearchParams, usePathname, useRouter } from 'expo-router';
import { useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import Svg, { Path } from 'react-native-svg';
import { NavBar, RowQuote, SectorIcon, Sticker, TabItem } from '@/components/ui';
import { useEtf } from '@/features/etf/queries';
import { PickGroupSheet } from '@/features/watch/PickGroupSheet';
import { useMembership } from '@/features/watch/queries';
import { createStyles, useColors } from '@/theme/theme';
import { fam } from '@/theme/typography';

const TABS = [
  { seg: 'brief', label: 'AI 분석' },
  { seg: 'summary', label: '오늘 움직임' },
  { seg: 'community', label: '커뮤니티' },
  { seg: 'data', label: '종목정보' },
] as const;

export default function EtfLayout() {
  const styles = useStyles();
  const colors = useColors();
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
      <NavBar title={data?.theme ?? ''} onBack={() => router.back()} rightIcon="search" onRight={() => router.push('/search')} />
      <View style={styles.head}>
        {data && <SectorIcon theme={data.theme} bg={data.logoBg} size={38} />}
        <View style={styles.mid}>
          <Text numberOfLines={1} style={styles.name}>{data?.name ?? code}</Text>
          {data && <RowQuote price={data.price} changePct={data.changePct} />}
        </View>
        {data && <Sticker signal={data.signal} />}
        <Pressable onPress={() => setPick(true)} hitSlop={6} style={styles.heart}>
          <Svg width={20} height={20} viewBox="0 0 24 24">
            <Path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z" fill={inWatch ? colors.up : 'none'} stroke={inWatch ? colors.up : colors.textDisabled} strokeWidth={1.8} strokeLinejoin="round" />
          </Svg>
        </Pressable>
      </View>
      <View style={styles.tabs}>
        {TABS.map((t) => (
          <TabItem key={t.seg} label={t.label} on={path.endsWith(`/${t.seg}`)} onPress={() => router.replace(`/etf/${code}/${t.seg}`)} />
        ))}
      </View>
      <Slot />
      <PickGroupSheet etfs={pick && data ? [data] : []} onClose={() => setPick(false)} />
    </View>
  );
}

const useStyles = createStyles((colors) => ({
  root: { flex: 1, backgroundColor: colors.bg },
  head: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingTop: 4, paddingBottom: 12, paddingHorizontal: 16 },
  mid: { flex: 1, gap: 5 },
  name: { fontFamily: fam.bold, fontSize: 15, color: colors.text, letterSpacing: -0.3, lineHeight: 20 },
  heart: { paddingVertical: 4, paddingLeft: 4 },
  tabs: { flexDirection: 'row', borderBottomWidth: 1, borderBottomColor: colors.line },
}));
