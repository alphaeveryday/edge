import { useRouter } from 'expo-router';
import { HowHero } from '@/features/onboarding/HowHero';
import { IntroPager, type IntroPage } from '@/features/onboarding/IntroPager';
import { StickerHero } from '@/features/onboarding/StickerHero';
import { WhatHero } from '@/features/onboarding/WhatHero';

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
    accent: '호재·차트·매크로·밸류·수급\n다섯 기준을 보고 정해요.',
    cta: '다음',
    hero: <StickerHero />,
  },
  {
    title: '오늘 달라진 것만\n보여드려요',
    body: '어떤 일이 생겼고, 그게 내 ETF에\n왜 중요한지 순서대로 설명해요.',
    accent: '어제 본 내용은 다시 안 읽어도 돼요.',
    cta: '내 ETF 고르기',
    hero: <WhatHero />,
  },
];

export default function Intro() {
  const router = useRouter();
  return <IntroPager pages={PAGES} onDone={() => router.push('/onboarding/theme')} />;
}
