import { useRouter } from 'expo-router';
import { StyleSheet, Text, View } from 'react-native';
import Svg, { Path } from 'react-native-svg';
import { IntroShell } from '@/features/onboarding/IntroShell';
import { colors } from '@/theme/tokens';
import { fam } from '@/theme/typography';

const PRESS_A = ['경향신문', '매일경제', '한국경제', '서울경제', '머니투데이', '이데일리'];
const PRESS_B = ['서울신문', '내일신문', '아주경제', 'SBS', 'YTN', '전자신문'];
const STATS = [
  { v: '70', l: '언론사', u: '빅카인즈 연동' },
  { v: '3.2만', l: '뉴스', u: '하루 평균' },
  { v: '410', l: '공시·리포트', u: '하루 평균' },
];

const Down = () => (
  <Svg width={16} height={22} viewBox="0 0 16 22">
    <Path d="M8 1v17M2 13l6 6 6-6" stroke={colors.lineStrong} strokeWidth={2} fill="none" strokeLinecap="round" strokeLinejoin="round" />
  </Svg>
);

export default function How() {
  const router = useRouter();
  return (
    <IntroShell
      step={0}
      title={'뉴스 3만 건을\n대신 읽어드려요'}
      body={'언론사 70곳의 뉴스와 공시, 리포트를\n매일 새벽에 모아요.'}
      accent={'최신 AI(GPT Astra)가\n다섯 가지 기준으로 정리해요.'}
      cta="다음"
      onNext={() => router.push('/onboarding/sticker')}
    >
      <View style={styles.col}>
        <View style={styles.press}>
          {[PRESS_A, PRESS_B].map((row, i) => (
            <View key={i} style={styles.pressRow}>
              {row.map((n, j) => (
                <View key={n} style={[styles.pressChip, (i + j) % 3 === 1 && styles.pressChipOn]}>
                  <Text style={[styles.pressText, (i + j) % 3 === 1 && { color: colors.white }]}>{n}</Text>
                </View>
              ))}
            </View>
          ))}
        </View>
        <Down />
        <View style={styles.stats}>
          {STATS.map((s) => (
            <View key={s.l} style={styles.stat}>
              <Text style={styles.statV}>{s.v}</Text>
              <Text style={styles.statL}>{s.l}</Text>
              <Text style={styles.statU}>{s.u}</Text>
            </View>
          ))}
        </View>
        <Down />
        <View style={styles.pill}>
          <View style={styles.pillDot}><View style={styles.pillDotIn} /></View>
          <Text style={styles.pillName}>GPT Astra</Text>
          <Text style={styles.pillSub}>최신 모델이 매일 새벽 읽어요</Text>
        </View>
      </View>
    </IntroShell>
  );
}

const styles = StyleSheet.create({
  col: { width: '100%', alignItems: 'center', gap: 16 },
  press: { gap: 8, alignSelf: 'stretch', overflow: 'hidden' },
  pressRow: { flexDirection: 'row', gap: 8, justifyContent: 'center' },
  pressChip: { paddingVertical: 7, paddingHorizontal: 12, borderRadius: 8, backgroundColor: colors.surface },
  pressChipOn: { backgroundColor: colors.text },
  pressText: { fontFamily: fam.extrabold, fontSize: 13, color: colors.textSub, letterSpacing: -0.26 },
  stats: { flexDirection: 'row', gap: 8, width: '100%', maxWidth: 330 },
  stat: { flex: 1, borderRadius: 14, backgroundColor: colors.card, paddingTop: 14, paddingBottom: 12, paddingHorizontal: 10, alignItems: 'center', gap: 3 },
  statV: { fontFamily: fam.monoExtraBold, fontSize: 22, color: colors.text, letterSpacing: -0.6 },
  statL: { fontFamily: fam.semibold, fontSize: 12, color: colors.textMuted },
  statU: { fontFamily: fam.regular, fontSize: 11, color: colors.textMuted },
  pill: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 12, paddingLeft: 14, paddingRight: 18, borderRadius: 999, backgroundColor: colors.text, shadowColor: colors.text, shadowOpacity: 0.22, shadowRadius: 15, shadowOffset: { width: 0, height: 12 } },
  pillDot: { width: 26, height: 26, borderRadius: 999, backgroundColor: colors.primary, alignItems: 'center', justifyContent: 'center' },
  pillDotIn: { width: 10, height: 10, borderRadius: 999, backgroundColor: colors.white },
  pillName: { fontFamily: fam.bold, fontSize: 14, color: colors.white, letterSpacing: -0.28 },
  pillSub: { fontFamily: fam.regular, fontSize: 12, color: colors.textMuted },
});
