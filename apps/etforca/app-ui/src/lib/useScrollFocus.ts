import { useEffect, useRef } from 'react';
import type { ScrollView, View } from 'react-native';

// 펼친 섹션의 화면 상단 이동
// offset 은 스크롤 위에 겹친 상단 바 높이
export function useScrollFocus(opened: boolean, offset = 0) {
  const scroll = useRef<ScrollView>(null);
  const anchor = useRef<View>(null);
  useEffect(() => {
    if (!opened) return;
    const id = requestAnimationFrame(() => {
      const s = scroll.current;
      // 네이티브와 웹 런타임에 있으나 타입 선언에만 빠진 메서드
      const content = (s as unknown as { getInnerViewRef?: () => View | null } | null)?.getInnerViewRef?.();
      if (!s || !content || !anchor.current) return;
      anchor.current.measureLayout(content, (_x, y) => s.scrollTo({ y: Math.max(0, y - offset), animated: true }));
    });
    return () => cancelAnimationFrame(id);
  }, [opened, offset]);
  return { scroll, anchor };
}
