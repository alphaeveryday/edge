import { StyleSheet, Text, View } from 'react-native';
import { ChangeOnly } from '@/components/ui';
import { colors, signal as SIG, SIGNAL_ORDER, type Signal, radius } from '@/theme/tokens';
import { fam } from '@/theme/typography';

const SEG_BG: Record<Signal, string> = {
  strongDown: colors.downDeep, down: colors.downLight, neutral: colors.lineStrong, up: colors.upLight, strongUp: colors.up,
};

// 관심 그룹 전체의 전망 강도 게이지
export function EdgeCard({ title, band, changePct }: { title: string; band: Signal; changePct: number }) {
  const i = SIGNAL_ORDER.indexOf(band);
  const pos = ((i + 0.5) / SIGNAL_ORDER.length) * 100;
  const c = SIG[band].color;
  return (
    <View style={styles.card}>
      <View style={styles.head}>
        <View style={styles.headL}>
          <Text style={styles.cap}>{title}</Text>
          <Text style={[styles.band, { color: SIG[band].labelColor }]}>{SIG[band].label}</Text>
        </View>
        <View style={styles.headR}>
          <Text style={styles.cap}>오늘 등락</Text>
          <ChangeOnly changePct={changePct} size={21} />
        </View>
      </View>
      <View style={styles.track}>
        <View style={styles.segs}>
          {SIGNAL_ORDER.map((s) => <View key={s} style={[styles.seg, { backgroundColor: SEG_BG[s] }]} />)}
        </View>
        <View style={[styles.knob, { left: `${pos}%`, borderColor: c }]} />
      </View>
      <View style={styles.ticks}>
        {SIGNAL_ORDER.map((s) => <Text key={s} style={styles.tick}>{SIG[s].label}</Text>)}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: { marginTop: 14, marginHorizontal: 20, borderRadius: radius.card, backgroundColor: colors.card, paddingTop: 17, paddingHorizontal: 17, paddingBottom: 16, gap: 14 },
  head: { flexDirection: 'row', alignItems: 'flex-end', gap: 12 },
  headL: { flex: 1, gap: 5 },
  headR: { alignItems: 'flex-end', gap: 4 },
  cap: { fontFamily: fam.regular, fontSize: 12, color: colors.textSub },
  band: { fontFamily: fam.extrabold, fontSize: 27, letterSpacing: -0.8, lineHeight: 30 },
  track: { marginTop: 2, height: 9, justifyContent: 'center' },
  segs: { flexDirection: 'row', gap: 3, height: 9 },
  seg: { flex: 1, borderRadius: 999 },
  knob: { position: 'absolute', top: -4, marginLeft: -8.5, width: 17, height: 17, borderRadius: 999, backgroundColor: colors.white, borderWidth: 3, shadowColor: '#000', shadowOpacity: 0.18, shadowRadius: 3, shadowOffset: { width: 0, height: 2 }, elevation: 3 },
  ticks: { flexDirection: 'row', justifyContent: 'space-between' },
  tick: { fontFamily: fam.regular, fontSize: 11, color: colors.textSub },
});
