import type { ReactNode } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { CtaButton } from '@/components/ui';
import { colors } from '@/theme/tokens';
import { fam } from '@/theme/typography';

interface Props {
  step: number; // 0..2
  title: string;
  body: string;
  accent: string;
  cta: string;
  onNext: () => void;
  children: ReactNode;
}

// 온보딩 1~3 공통 틀: 상단 일러스트 영역 + 문구 + 점 + CTA
export function IntroShell({ step, title, body, accent, cta, onNext, children }: Props) {
  const { top, bottom } = useSafeAreaInsets();
  return (
    <View style={[styles.root, { paddingTop: top + 16 }]}>
      <View style={styles.hero}>{children}</View>
      <View style={styles.copy}>
        <Text style={styles.title}>{title}</Text>
        <Text style={styles.body}>{body}</Text>
        <Text style={styles.accent}>{accent}</Text>
      </View>
      <View style={styles.dots}>
        {[0, 1, 2].map((i) => <View key={i} style={[styles.dot, i === step && styles.dotOn]} />)}
      </View>
      <View style={[styles.cta, { paddingBottom: Math.max(bottom, 16) + 14 }]}>
        <CtaButton label={cta} onPress={onNext} />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.white },
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
