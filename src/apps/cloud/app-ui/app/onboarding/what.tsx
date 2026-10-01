import { useRouter } from 'expo-router';
import { StyleSheet, Text, View } from 'react-native';
import { IntroShell } from '@/features/onboarding/IntroShell';
import { colors } from '@/theme/tokens';
import { fam } from '@/theme/typography';

const RANK = [
  { n: '01', name: '반도체TOP10', bg: '#3D34E0', sig: '▲ 강력 상승', sc: '#F04452' },
  { n: '02', name: 'K방산', bg: '#131318', sig: '▲ 강력 상승', sc: '#F04452' },
  { n: '03', name: '미국AI전력', bg: '#0E8A6C', sig: '▲ 상승', sc: '#F04452' },
  { n: '04', name: '국고채30년', bg: '#E0562B', sig: '■ 중립', sc: '#8E8E93' },
];

export default function What() {
  const router = useRouter();
  return (
    <IntroShell
      step={2}
      title={'오늘 달라진 것만\n보여드려요'}
      body={'어떤 일이 생겼고, 그게 내 ETF에\n왜 중요한지 순서대로 설명해요.'}
      accent="어제 본 내용은 다시 안 읽어도 돼요."
      cta="내 ETF 고르기"
      onNext={() => router.push('/onboarding/theme')}
    >
      <View style={styles.stage}>
        <View style={styles.rankCard}>
          <Text style={styles.rankTitle}>전망 좋은 순</Text>
          {RANK.map((r) => (
            <View key={r.n} style={styles.rankRow}>
              <Text style={styles.rankN}>{r.n}</Text>
              <View style={[styles.rankDot, { backgroundColor: r.bg }]} />
              <Text numberOfLines={1} style={styles.rankName}>{r.name}</Text>
              <Text style={[styles.rankSig, { color: r.sc }]}>{r.sig}</Text>
            </View>
          ))}
        </View>
        <View style={styles.mainCard}>
          <View style={styles.mainHead}>
            <View style={styles.mainLogo} />
            <View style={{ flex: 1, gap: 2 }}>
              <Text numberOfLines={1} style={styles.mainName}>TIGER 반도체TOP10</Text>
              <Text style={styles.mainPrice}>₩ 12,819 <Text style={{ color: colors.up }}>+3.0%</Text></Text>
            </View>
            <View style={styles.mainSig}><Text style={styles.mainSigText}>▲ 강력 상승</Text></View>
          </View>
          <View style={styles.today}>
            <Text style={styles.todayHead}>● 오늘 추가된 것 · 9월 5일</Text>
            <Text style={styles.todayLine}>SK하이닉스 영업이익률은 42%에서 49%가 됐어요.</Text>
            <Text style={styles.todayLine}>SK하이닉스가 HBM4를 고객 3곳에 처음 출하했어요.</Text>
          </View>
          <View style={{ gap: 10 }}>
            <View style={styles.pt}>
              <Text style={styles.ptN}>01</Text>
              <View style={{ flex: 1, gap: 6 }}>
                <Text style={styles.ptTitle}>서버용 D램 값이 3개월 동안 18% 올랐어요</Text>
                <View style={styles.ptTag}><Text style={styles.ptTagText}>이슈 <Text style={{ color: colors.up }}>● 도움</Text></Text></View>
              </View>
            </View>
            <View style={styles.pt}>
              <Text style={styles.ptN}>02</Text>
              <View style={{ flex: 1, gap: 6 }}>
                <Text style={styles.ptTitle}>SK하이닉스 이익률이 42%에서 49%가 됐어요</Text>
                <Text style={styles.ptBody}>• 메모리는 값이 오르면 이익이 그대로 늘어요.{'\n'}• 공장을 더 짓지 않아도 같은 칩을 더 비싸게 팔기 때문이에요.</Text>
              </View>
            </View>
          </View>
        </View>
      </View>
    </IntroShell>
  );
}

const styles = StyleSheet.create({
  stage: { width: '100%', height: 340, alignItems: 'center', justifyContent: 'center' },
  rankCard: { position: 'absolute', left: -6, top: 60, width: 168, borderRadius: 16, backgroundColor: colors.white, borderWidth: 1, borderColor: colors.line, padding: 14, gap: 10, transform: [{ rotate: '-9deg' }], shadowColor: colors.text, shadowOpacity: 0.1, shadowRadius: 17, shadowOffset: { width: 0, height: 14 } },
  rankTitle: { fontFamily: fam.extrabold, fontSize: 12, color: colors.text },
  rankRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  rankN: { fontFamily: fam.monoBold, fontSize: 10, color: colors.textFaint, width: 14 },
  rankDot: { width: 18, height: 18, borderRadius: 999 },
  rankName: { flex: 1, fontFamily: fam.bold, fontSize: 11, color: colors.text },
  rankSig: { fontFamily: fam.extrabold, fontSize: 10 },
  mainCard: { position: 'absolute', right: -30, top: 10, width: 258, borderRadius: 22, backgroundColor: colors.white, borderWidth: 1, borderColor: colors.line, paddingTop: 18, paddingHorizontal: 16, paddingBottom: 30, gap: 14, transform: [{ rotate: '7deg' }], shadowColor: colors.text, shadowOpacity: 0.14, shadowRadius: 25, shadowOffset: { width: 0, height: 24 } },
  mainHead: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  mainLogo: { width: 32, height: 32, borderRadius: 999, backgroundColor: colors.primary },
  mainName: { fontFamily: fam.bold, fontSize: 14, color: colors.text },
  mainPrice: { fontFamily: fam.mono, fontSize: 11, color: colors.textFaint },
  mainSig: { backgroundColor: 'rgba(240,68,82,0.12)', borderRadius: 999, paddingVertical: 5, paddingHorizontal: 8 },
  mainSigText: { fontFamily: fam.extrabold, fontSize: 11, color: colors.up },
  today: { borderRadius: 14, backgroundColor: colors.primarySoft, paddingHorizontal: 12, paddingTop: 12, paddingBottom: 11, gap: 8 },
  todayHead: { fontFamily: fam.extrabold, fontSize: 11, color: colors.primary },
  todayLine: { alignSelf: 'flex-start', fontFamily: fam.regular, fontSize: 12, lineHeight: 17, color: colors.text, backgroundColor: '#DDD9FB', paddingHorizontal: 4, paddingVertical: 2, borderRadius: 4 },
  pt: { flexDirection: 'row', gap: 8, alignItems: 'flex-start' },
  ptN: { fontFamily: fam.mono, fontSize: 10, color: colors.textFaint, paddingTop: 3 },
  ptTitle: { fontFamily: fam.bold, fontSize: 13, lineHeight: 18, color: colors.text },
  ptTag: { alignSelf: 'flex-start', backgroundColor: colors.surface, borderRadius: 6, paddingVertical: 3, paddingHorizontal: 7 },
  ptTagText: { fontFamily: fam.bold, fontSize: 11, color: colors.textSub },
  ptBody: { fontFamily: fam.regular, fontSize: 12, lineHeight: 18, color: colors.textSub },
});
