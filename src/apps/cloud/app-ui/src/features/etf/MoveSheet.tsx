import { useRouter } from 'expo-router';
import { ScrollView, StyleSheet, Text, View } from 'react-native';
import type { EtfSummary, MoveInfo } from '@/api';
import { BottomSheet, CtaButton, IconButton, Sticker } from '@/components/ui';
import { dirSignal } from '@/features/analysis/dir';
import { colors, signal as SIG } from '@/theme/tokens';
import { fam } from '@/theme/typography';

interface Props {
  etf: EtfSummary;
  move: MoveInfo | null;
  onClose: () => void;
}

// 오늘 움직임의 원인 상세 시트
export function MoveSheet({ etf, move, onClose }: Props) {
  const router = useRouter();
  return (
    <BottomSheet open={!!move} onClose={onClose}>
      <View style={styles.head}>
        <Sticker signal={etf.signal} />
        <View style={{ flex: 1 }} />
        <IconButton icon="close" size={32} color={colors.textFaint} onPress={onClose} />
      </View>
      <ScrollView showsVerticalScrollIndicator={false}>
        <Text style={styles.title}>{move?.sheetTitle}</Text>
        <View style={{ gap: 22, marginTop: 22 }}>
          {move?.groups.map((g) => (
            <View key={g.head} style={{ gap: 12 }}>
              <Text style={styles.groupHead}>{g.head}</Text>
              {g.items.map((it) => (
                <View key={it.t} style={styles.item}>
                  <Text style={[styles.mark, { color: SIG[dirSignal[it.dir]].color }]}>{SIG[dirSignal[it.dir]].mark}</Text>
                  <View style={{ flex: 1, gap: 3 }}>
                    <Text style={styles.itemT}>{it.t}</Text>
                    {!!it.sub && <Text style={styles.itemSub}>{it.sub}</Text>}
                  </View>
                </View>
              ))}
            </View>
          ))}
        </View>
        <View style={{ marginTop: 24 }}>
          <CtaButton label="데일리 분석 보기" tone="dark" onPress={() => { onClose(); router.replace(`/etf/${etf.code}/brief`); }} />
        </View>
      </ScrollView>
    </BottomSheet>
  );
}

const styles = StyleSheet.create({
  head: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  title: { fontFamily: fam.extrabold, fontSize: 20, lineHeight: 28, letterSpacing: -0.6, color: colors.text, marginTop: 12 },
  groupHead: { fontFamily: fam.extrabold, fontSize: 13, color: colors.neutral },
  item: { flexDirection: 'row', gap: 9, alignItems: 'flex-start' },
  mark: { width: 11, textAlign: 'center', fontFamily: fam.extrabold, fontSize: 11, marginTop: 6 },
  itemT: { fontFamily: fam.bold, fontSize: 15, lineHeight: 22, letterSpacing: -0.3, color: colors.text },
  itemSub: { fontFamily: fam.regular, fontSize: 14, lineHeight: 22, color: colors.textSub },
});
