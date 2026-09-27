import type { ReactNode } from 'react';
import { StyleSheet, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { CtaButton, NavBar, PageTitle } from '@/components/ui';
import { colors } from '@/theme/tokens';

interface Props {
  navTitle?: string;
  title: string;
  sub: string;
  cta: string;
  ctaDisabled?: boolean;
  onBack: () => void;
  onNext: () => void;
  children: ReactNode;
}

// 테마 선택 · ETF 선택 공통 틀: 네비 + 제목 + 본문 + 하단 CTA
export function PickShell({ navTitle = '', title, sub, cta, ctaDisabled, onBack, onNext, children }: Props) {
  const { top, bottom } = useSafeAreaInsets();
  return (
    <View style={[styles.root, { paddingTop: top + 8 }]}>
      <NavBar title={navTitle} onBack={onBack} />
      <PageTitle title={title} sub={sub} />
      <View style={styles.body}>{children}</View>
      <View style={[styles.foot, { paddingBottom: Math.max(bottom, 16) + 14 }]}>
        <CtaButton label={cta} disabled={ctaDisabled} onPress={onNext} />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.white },
  body: { flex: 1 },
  foot: { paddingTop: 16, paddingHorizontal: 16, backgroundColor: colors.card },
});
