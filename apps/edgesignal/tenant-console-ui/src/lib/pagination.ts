/* 목록 무한 스크롤 공용 유틸(ALPHA-914) — 서버 limit/offset 페이지와 하단 센티널. */
import { useEffect, useRef, useState } from 'react';

/** 서버 목록 페이지 크기 — 검수·설명 목록이 공유한다(tenant-console-api 기본값과 동일). */
export const LIST_PAGE_SIZE = 50;

/**
 * 마지막 페이지가 꽉 찼으면 다음 offset(=로드된 총 건수), 아니면 끝.
 * useInfiniteQuery 의 getNextPageParam 에 그대로 넣는다.
 */
export function nextOffset<T>(lastPage: T[], allPages: T[][]): number | undefined {
  return lastPage.length === LIST_PAGE_SIZE
    ? allPages.reduce((n, p) => n + p.length, 0)
    : undefined;
}

/**
 * 목록 하단 센티널이 뷰포트에 들어오면 loadMore 를 부른다. 반환값을 센티널 요소의
 * ref 로 건다. enabled(다음 페이지 존재 && 로딩 중 아님)가 false 면 관찰하지 않는다.
 */
export function useInfiniteScroll(loadMore: () => void, enabled: boolean) {
  // 콜백은 ref 로 고정 — loadMore 정체성 변화로 observer 를 재부착하지 않는다.
  const cb = useRef(loadMore);
  cb.current = loadMore;
  const [node, setNode] = useState<Element | null>(null);

  useEffect(() => {
    if (!node || !enabled) return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) cb.current();
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [node, enabled]);

  return setNode;
}
