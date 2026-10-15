import { useRouter } from 'expo-router';
import { Pressable, Text, View } from 'react-native';
import { HowHero } from '@/features/onboarding/HowHero';
import { IntroPager, type IntroPage } from '@/features/onboarding/IntroPager';
import { StickerHero } from '@/features/onboarding/StickerHero';
import { createStyles } from '@/theme/theme';
import { fam } from '@/theme/typography';

const PAGES: IntroPage[] = [
  {
    title: '뉴스 3만 건을\n대신 읽어드려요',
    body: '언론사 70곳의 뉴스와 공시, 리포트를\n매일 새벽에 모아요.',
    accent: 'AI가 다섯 가지 기준으로 정리해요.',
    cta: '다음',
    hero: <HowHero />,
  },
  {
    title: '전망은 스티커\n하나로 말해요',
    body: '강력 하락부터 강력 상승까지 5단계.\nETF마다 매일 아침 하나씩 붙어요.',
    accent: '이슈·차트·매크로·밸류·수급\n다섯 기준을 보고 정해요.',
    cta: '관심 ETF 고르기',
    hero: <StickerHero />,
  },
];

// 재설치·새 기기 회원의 고르기 생략용 로그인 진입점
export default function Intro() {
  const styles = useStyles();
  const router = useRouter();
  const login = (
    <View style={styles.toLogin}>
      <Text style={styles.toLoginText}>이미 계정이 있나요?</Text>
      <Pressable onPress={() => router.push({ pathname: '/login', params: { reason: '온보딩' } })} hitSlop={8}><Text style={styles.toLoginLink}>로그인</Text></Pressable>
    </View>
  );
  return <IntroPager pages={PAGES} onDone={() => router.push('/onboarding/etf')} footer={login} />;
}

const useStyles = createStyles((colors) => ({
  toLogin: { flexDirection: 'row', justifyContent: 'center', gap: 6 },
  toLoginText: { fontFamily: fam.regular, fontSize: 14, color: colors.textMuted },
  toLoginLink: { fontFamily: fam.bold, fontSize: 14, color: colors.primary },
}));
