import { useFocusEffect } from 'expo-router';
import { type ReactNode, useCallback, useRef, useState } from 'react';
import { BackHandler, type NativeScrollEvent, type NativeSyntheticEvent, ScrollView, StyleSheet, Text, useWindowDimensions, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { CtaButton } from '@/components/ui';
import { colors } from '@/theme/tokens';
import { fam } from '@/theme/typography';

export interface IntroPage {
  title: string;
  body: string;
  accent: string;
  cta: string;
  hero: ReactNode;
}

interface Props {
  pages: IntroPage[];
  onDone: () => void;
}

// 온보딩 소개 가로 페이저, 점 표시와 버튼 고정
export function IntroPager({ pages, onDone }: Props) {
  const { top, bottom } = useSafeAreaInsets();
  const { width } = useWindowDimensions();
  const [height, setHeight] = useState(0);
  const [index, setIndex] = useState(0);
  const scroll = useRef<ScrollView>(null);

  const go = useCallback((i: number) => scroll.current?.scrollTo({ x: i * width, animated: true }), [width]);
  const onScroll = (e: NativeSyntheticEvent<NativeScrollEvent>) => {
    const i = Math.round(e.nativeEvent.contentOffset.x / width);
    if (i !== index && i >= 0 && i < pages.length) setIndex(i);
  };

  // Android 뒤로 가기는 이전 장
  useFocusEffect(useCallback(() => {
    const sub = BackHandler.addEventListener('hardwareBackPress', () => {
      if (index === 0) return false;
      go(index - 1);
      return true;
    });
    return () => sub.remove();
  }, [index, go]));

  const page = pages[index];
  return (
    <View style={[styles.root, { paddingTop: top + 16 }]}>
      <ScrollView
        ref={scroll}
        horizontal
        pagingEnabled
        showsHorizontalScrollIndicator={false}
        scrollEventThrottle={16}
        onScroll={onScroll}
        onLayout={(e) => setHeight(e.nativeEvent.layout.height)}
        style={styles.pager}
      >
        {pages.map((p) => (
          <View key={p.title} style={{ width, height }}>
            <View style={styles.hero}>{p.hero}</View>
            <View style={styles.copy}>
              <Text style={styles.title}>{p.title}</Text>
              <Text style={styles.body}>{p.body}</Text>
              <Text style={styles.accent}>{p.accent}</Text>
            </View>
          </View>
        ))}
      </ScrollView>
      <View style={styles.dots}>
        {pages.map((p, i) => <View key={p.title} style={[styles.dot, i === index && styles.dotOn]} />)}
      </View>
      <View style={[styles.cta, { paddingBottom: Math.max(bottom, 16) + 14 }]}>
        <CtaButton label={page.cta} onPress={() => (index < pages.length - 1 ? go(index + 1) : onDone())} />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.white },
  pager: { flex: 1 },
  hero: { flex: 1, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 24, overflow: 'hidden' },
  copy: { paddingHorizontal: 28, paddingBottom: 22, alignItems: 'center' },
  title: { fontFamily: fam.extrabold, fontSize: 26, lineHeight: 33, letterSpacing: -0.8, color: colors.text, textAlign: 'center' },
  body: { fontFamily: fam.regular, fontSize: 15, lineHeight: 24, color: colors.textMuted, marginTop: 12, textAlign: 'center' },
  accent: { fontFamily: fam.semibold, fontSize: 15, lineHeight: 23, color: colors.primary, marginTop: 12, textAlign: 'center' },
  dots: { flexDirection: 'row', justifyContent: 'center', gap: 6, paddingBottom: 18 },
  dot: { width: 6, height: 6, borderRadius: 999, backgroundColor: colors.line },
  dotOn: { width: 18, backgroundColor: colors.primary },
  cta: { paddingHorizontal: 16 },
});
