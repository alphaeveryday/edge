import { Redirect, useLocalSearchParams, useRouter } from 'expo-router';
import { Placeholder } from '@/components/Placeholder';
import { useSession } from '@/store/session';

const STEPS = ['how', 'sticker', 'what', 'theme', 'etf'] as const;
type Step = (typeof STEPS)[number];
const TITLES: Record<Step, string> = {
  how: '온보딩 1 · 뉴스 3만 건을 대신 읽어드려요',
  sticker: '온보딩 2 · 전망은 스티커 하나로 말해요',
  what: '온보딩 3 · 오늘 달라진 것만 보여드려요',
  theme: '관심 있는 테마를 골라주세요',
  etf: '지켜볼 ETF를 골라주세요',
};

export default function OnboardingStep() {
  const { step } = useLocalSearchParams<{ step: string }>();
  const router = useRouter();
  const finish = useSession((s) => s.finishOnboarding);
  if (!STEPS.includes(step as Step)) return <Redirect href="/onboarding/how" />;
  const i = STEPS.indexOf(step as Step);
  const next = STEPS[i + 1];
  return (
    <Placeholder
      title={TITLES[step as Step]}
      links={
        next
          ? [{ label: '다음', href: `/onboarding/${next}` }]
          : [{ label: '로그인으로', href: '/login' }]
      }
    />
  );
}
