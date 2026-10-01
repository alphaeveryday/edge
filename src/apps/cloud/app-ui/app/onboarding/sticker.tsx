import { useRouter } from 'expo-router';
import { StyleSheet, Text, View } from 'react-native';
import Svg, { Path } from 'react-native-svg';
import { Sticker } from '@/components/ui';
import { IntroShell } from '@/features/onboarding/IntroShell';
import { colors, SIGNAL_ORDER } from '@/theme/tokens';
import { fam } from '@/theme/typography';

const HINT: Record<string, string> = { strongUp: '지금', neutral: '지켜봐요', strongDown: '피해요' };
const AXES = [
  { name: '호재', q: '사건이 진짜인가', c: colors.up },
  { name: '차트', q: '주가가 따라왔나', c: colors.down },
  { name: '매크로', q: '밖에서 방해하나', c: colors.warn },
  { name: '밸류', q: '이익 대비 싼가', c: '#8B34E0' },
  { name: '수급', q: '큰손이 믿나', c: colors.positive },
];

export default function StickerIntro() {
  const router = useRouter();
  return (
    <IntroShell
      step={1}
      title={'전망은 스티커\n하나로 말해요'}
      body={'강력 하락부터 강력 상승까지 5단계.\nETF마다 매일 아침 하나씩 붙어요.'}
      accent={'호재·차트·매크로·밸류·수급\n다섯 기준을 보고 정해요.'}
      cta="다음"
      onNext={() => router.push('/onboarding/what')}
    >
      <View style={styles.glow} />
      <View style={styles.stack}>
        {[...SIGNAL_ORDER].reverse().map((s, i) => (
          <View key={s} style={[styles.row, { transform: [{ scale: i === 0 || i === 4 ? 1 : 0.92 }] }]}>
            <Text style={styles.hint}>{HINT[s] ?? ''}</Text>
            <Sticker signal={s} size={34} radius={11} />
            <View style={{ width: 44 }} />
          </View>
        ))}
      </View>
      <View style={styles.axesWrap}>
        <Svg width={16} height={22} viewBox="0 0 16 22" style={{ transform: [{ rotate: '180deg' }] }}>
          <Path d="M8 1v17M2 13l6 6 6-6" stroke={colors.lineStrong} strokeWidth={2} fill="none" strokeLinecap="round" strokeLinejoin="round" />
        </Svg>
        <View style={styles.axes}>
          {AXES.map((a) => (
            <View key={a.name} style={styles.axis}>
              <View style={[styles.axisDot, { backgroundColor: a.c }]} />
              <Text style={styles.axisName}>{a.name}</Text>
              <Text style={styles.axisQ}>{a.q}</Text>
            </View>
          ))}
        </View>
      </View>
    </IntroShell>
  );
}

const styles = StyleSheet.create({
  glow: { position: 'absolute', width: 320, height: 320, borderRadius: 999, backgroundColor: colors.primarySoft, opacity: 0.7 },
  stack: { alignItems: 'center', gap: 9 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  hint: { width: 44, textAlign: 'right', fontFamily: fam.semibold, fontSize: 12, color: colors.textFaint },
  axesWrap: { alignItems: 'center', gap: 10, marginTop: 22 },
  axes: { flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'center', gap: 6, maxWidth: 330 },
  axis: { flexDirection: 'row', alignItems: 'center', gap: 6, backgroundColor: colors.surface, borderRadius: 999, paddingVertical: 7, paddingLeft: 9, paddingRight: 12 },
  axisDot: { width: 7, height: 7, borderRadius: 999 },
  axisName: { fontFamily: fam.bold, fontSize: 13, color: colors.text },
  axisQ: { fontFamily: fam.medium, fontSize: 13, color: colors.textMuted },
});
