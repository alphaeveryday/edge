import { Slot, useLocalSearchParams, usePathname, useRouter } from 'expo-router';
import { useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Icon } from '@/components/ui/Icon';
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
          <Icon name="heart" color={inWatch ? colors.up : colors.textDisabled} fill={inWatch ? colors.up : 'none'} size={21} />
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
