import { useRouter } from 'expo-router';
import { useEffect, useState } from 'react';
import { useEtfList } from '@/features/etf/queries';
import { EtfPickGrid } from '@/features/onboarding/EtfPickGrid';
import { PickShell } from '@/features/onboarding/PickShell';
import { useSetMembers, useWatchGroups, useWatchList } from '@/features/watch/queries';
import { useToast } from '@/store/toast';
import { useWatchGroup } from '@/store/watch';

// 관심 그룹에 담을 ETF 선택
export default function WatchAdd() {
  const router = useRouter();
  const group = useWatchGroup((s) => s.group);
  const { data: groups } = useWatchGroups();
  const { data: all } = useEtfList();
  const { data: current } = useWatchList(group);
  const save = useSetMembers();
  const toast = useToast((s) => s.show);
  const [picked, setPicked] = useState<string[] | null>(null);
  useEffect(() => {
    if (current && picked === null) setPicked(current.map((e) => e.code));
  }, [current, picked]);
  const codes = picked ?? [];
  const label = groups?.find((g) => g.key === group)?.label ?? '기본 관심';
  const toggle = (c: string) => setPicked((p) => ((p ?? []).includes(c) ? (p ?? []).filter((x) => x !== c) : [...(p ?? []), c]));
  const done = () =>
    save.mutate({ group, codes }, {
      onSuccess: () => {
        router.back();
        toast(`${codes.length}개를 ${label}에 담았어요`);
      },
    });
  return (
    <PickShell
      navTitle="종목 추가"
      title={`${label}에 담을 ETF`}
      sub="누르면 담기고, 다시 누르면 빠져요."
      cta={`${codes.length}개 담기`}
      ctaDisabled={codes.length === 0 || save.isPending}
      onBack={() => router.back()}
      onNext={done}
    >
      <EtfPickGrid etfs={all ?? []} picked={codes} onToggle={toggle} />
    </PickShell>
  );
}
