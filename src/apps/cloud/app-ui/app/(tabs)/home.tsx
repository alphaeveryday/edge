import { Placeholder } from '@/components/Placeholder';
import { useWatchEtfs } from '@/features/etf/queries';

export default function Home() {
  const { data } = useWatchEtfs();
  return (
    <Placeholder
      title="홈 · 내 종목 브리핑"
      links={[
        ...(data ?? []).map((e) => ({ label: `${e.name} ${e.changePct > 0 ? '+' : ''}${e.changePct}%`, href: `/etf/${e.code}/brief` as const })),
        { label: '스토리', href: '/story/AXAI' },
        { label: '검색', href: '/search' },
        { label: '알림', href: '/notifications' },
        { label: '전체 메뉴 · 계정', href: '/profile' },
      ]}
    />
  );
}
