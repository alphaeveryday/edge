import { Placeholder } from '@/components/Placeholder';

export default function Explore() {
  return (
    <Placeholder
      title="탐색 · AI가 보는 오늘 순위"
      links={[
        { label: '테마 ETF 비교', href: '/(tabs)/explore/compare' },
        { label: '테마 목록', href: '/(tabs)/explore/themes' },
        { label: '이슈', href: '/issues' },
      ]}
    />
  );
}
