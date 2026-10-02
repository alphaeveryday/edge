import { useInfiniteQuery, type QueryKey } from '@tanstack/react-query';
import type { NativeScrollEvent, NativeSyntheticEvent } from 'react-native';
import type { Page } from '@/api';

// 커서 목록의 이어 받기, data 는 펼친 배열
export function usePages<T>(queryKey: QueryKey, fetch: (cursor?: string) => Promise<Page<T>>) {
  return useInfiniteQuery({
    queryKey,
    queryFn: ({ pageParam }) => fetch(pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next ?? undefined,
    select: (d) => d.pages.flatMap((p) => p.items),
  });
}

interface More {
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  fetchNextPage: () => unknown;
}

// 스크롤 끝 근처에서 다음 페이지 받기
export const loadMore = (q: More) => ({
  scrollEventThrottle: 100,
  onScroll: (e: NativeSyntheticEvent<NativeScrollEvent>) => {
    const { layoutMeasurement, contentOffset, contentSize } = e.nativeEvent;
    if (layoutMeasurement.height + contentOffset.y < contentSize.height - 600) return;
    if (q.hasNextPage && !q.isFetchingNextPage) q.fetchNextPage();
  },
});
