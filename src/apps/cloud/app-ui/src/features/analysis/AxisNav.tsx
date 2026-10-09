import { useRouter } from 'expo-router';
import { Text, View } from 'react-native';
import type { Axis, Dir } from '@/api';
import { IconButton, Sticker } from '@/components/ui';
import { useEtf } from '@/features/etf/queries';
import { createStyles } from '@/theme/theme';
import { fam } from '@/theme/typography';
import { dirSignal } from './dir';

// 축 상세 화면 공용 상단 바
export function AxisNav({ code, axis, dir }: { code: string; axis: Axis; dir?: Dir }) {
  const styles = useStyles();
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

const useStyles = createStyles((colors) => ({
  nav: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 8, paddingTop: 2 },
  etf: { flex: 1, fontFamily: fam.regular, fontSize: 13, color: colors.textMuted },
}));
