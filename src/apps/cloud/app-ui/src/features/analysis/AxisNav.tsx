import { useRouter } from 'expo-router';
import { StyleSheet, Text, View } from 'react-native';
import type { Axis, Dir } from '@/api';
import { IconButton, Sticker } from '@/components/ui';
import { useEtf } from '@/features/etf/queries';
import { colors } from '@/theme/tokens';
import { fam } from '@/theme/typography';
import { dirSignal } from './dir';

// 축 상세 화면 공용 상단, 뒤로·축 배지·ETF 이름
export function AxisNav({ code, axis, dir }: { code: string; axis: Axis; dir?: Dir }) {
  const router = useRouter();
  const { data: etf } = useEtf(code);
  return (
    <View style={styles.nav}>
      <IconButton icon="back" onPress={() => router.back()} />
      {dir && <Sticker signal={dirSignal[dir]} size={28} radius={11} label={axis} />}
      <Text numberOfLines={1} style={styles.etf}>{etf?.name}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  nav: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 8, paddingTop: 2 },
  etf: { flex: 1, fontFamily: fam.regular, fontSize: 13, color: colors.textMuted },
});
