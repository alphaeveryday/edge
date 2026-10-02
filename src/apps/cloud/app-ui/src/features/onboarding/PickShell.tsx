import type { ReactNode } from 'react';
import { StyleSheet, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { BottomBar, CtaButton, NavBar, PageTitle } from '@/components/ui';
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

// 테마 선택과 ETF 선택 공통 틀
export function PickShell({ navTitle = '', title, sub, cta, ctaDisabled, onBack, onNext, children }: Props) {
  const { top } = useSafeAreaInsets();
  return (
    <View style={[styles.root, { paddingTop: top + 8 }]}>
      <NavBar title={navTitle} onBack={onBack} />
      <PageTitle title={title} sub={sub} />
      <View style={styles.body}>{children}</View>
      <BottomBar style={styles.foot}>
        <CtaButton label={cta} disabled={ctaDisabled} onPress={onNext} />
      </BottomBar>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.white },
  body: { flex: 1 },
  foot: { paddingTop: 12, paddingHorizontal: 16, borderTopWidth: 1, borderTopColor: colors.surface },
});
