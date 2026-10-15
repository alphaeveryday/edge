import { useRouter } from 'expo-router';
import { useEtfList } from '@/features/etf/queries';
import { EtfPickGrid } from '@/features/onboarding/EtfPickGrid';
import { PickShell } from '@/features/onboarding/PickShell';
import { api } from '@/api';
import { track } from '@/lib/analytics';
import { useOnboarding } from '@/store/onboarding';
import { useSession } from '@/store/session';


export default function EtfPick() {
  const router = useRouter();
  const { data } = useEtfList();
  const { etfs, toggleEtf } = useOnboarding();
  const finishOnboarding = useSession((s) => s.finishOnboarding);
  const done = async () => {
    await api.onboarding.complete({ themes: [], etfs });
    track('onboarding_completed', { etf_count: etfs.length });
    finishOnboarding();
    // 고른 ETF 중 목록상 가장 앞의 ETF
    const first = (data ?? []).find((e) => etfs.includes(e.code))?.code ?? etfs[0];
    router.replace('/(tabs)/home');
    router.push(`/etf/${first}/brief`);
  };
  return (
    <PickShell
      navTitle=""
      title="관심 ETF를 골라보세요."
      cta="전망 보기"
      ctaDisabled={etfs.length === 0}
      onBack={() => router.back()}
      onNext={done}
    >
      <EtfPickGrid etfs={data ?? []} picked={etfs} onToggle={toggleEtf} />
    </PickShell>
  );
}
