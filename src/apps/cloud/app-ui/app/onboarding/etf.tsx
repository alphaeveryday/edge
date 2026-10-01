import { useRouter } from 'expo-router';
import { useMemo } from 'react';
import { useEtfList, useThemes } from '@/features/etf/queries';
import { EtfPickGrid } from '@/features/onboarding/EtfPickGrid';
import { PickShell } from '@/features/onboarding/PickShell';
import { api } from '@/api';
import { useOnboarding } from '@/store/onboarding';
import { useSession } from '@/store/session';


export default function EtfPick() {
  const router = useRouter();
  const { data } = useEtfList();
  const { data: themeList } = useThemes();
  const { themes, etfs, toggleEtf } = useOnboarding();
  // ETF 테마 표기에 맞춘 고른 테마의 라벨
  const picked = useMemo(() => (themeList ?? []).filter((t) => themes.includes(t.key)).map((t) => t.label), [themeList, themes]);
  const finishOnboarding = useSession((s) => s.finishOnboarding);
  const ranked = useMemo(() => [...(data ?? [])].sort((a, b) => Number(picked.includes(b.theme)) - Number(picked.includes(a.theme))), [data, picked]);
  const n = etfs.length;
  const done = async () => {
    await api.onboarding.complete({ themes, etfs });
    finishOnboarding();
    router.replace('/(tabs)/home');
  };
  return (
    <PickShell
      navTitle=""
      title="지켜볼 ETF를 골라주세요"
      sub={picked.length ? `${picked.slice(0, 2).join(' · ')} ETF를 먼저 보여드려요.` : '전망이 좋은 ETF부터 보여드려요.'}
      cta={`${n}개 선택`}
      ctaDisabled={n === 0}
      onBack={() => router.back()}
      onNext={done}
    >
      <EtfPickGrid etfs={ranked} picked={etfs} onToggle={toggleEtf} />
    </PickShell>
  );
}

