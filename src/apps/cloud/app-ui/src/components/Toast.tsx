import { Fragment, useEffect, useMemo, useRef } from 'react';
import { Animated, PanResponder, Platform, StyleSheet, Text, View } from 'react-native';
import { FullWindowOverlay } from 'react-native-screens';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import Svg, { Path } from 'react-native-svg';
import { useToast } from '@/store/toast';
import { colors, shadow, radius } from '@/theme/tokens';
import { fam } from '@/theme/typography';

// 전체 화면 모달과 시트 위에도 보이도록 iOS 는 창 최상단 레이어에 표시
const Layer = Platform.OS === 'ios' ? FullWindowOverlay : Fragment;

export function Toast() {
  const text = useToast((s) => s.text);
  const kind = useToast((s) => s.kind);
  const hide = useToast((s) => s.hide);
  const { top } = useSafeAreaInsets();
  const y = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    if (text) y.setValue(0);
  }, [text, y]);
  // 위로 밀어 닫기
  const pan = useMemo(() => PanResponder.create({
    onMoveShouldSetPanResponder: (_, g) => g.dy < -4,
    onPanResponderMove: (_, g) => y.setValue(Math.min(0, g.dy)),
    onPanResponderRelease: (_, g) => {
      if (g.dy < -24 || g.vy < -0.5) hide();
      else Animated.spring(y, { toValue: 0, useNativeDriver: true, bounciness: 0 }).start();
    },
  }), [y, hide]);
  if (!text) return null;
  const error = kind === 'error';
  return (
    <Layer>
      <Animated.View {...pan.panHandlers} style={[styles.root, { top: top + 8, transform: [{ translateY: y }] }]}>
        <View style={[styles.icon, { backgroundColor: error ? colors.up : colors.success }]}>
          <Svg width={13} height={13} viewBox="0 0 14 14">
            {error ? (
              <Path d="M7 3.2v4.6M7 10.6v.1" stroke={colors.white} strokeWidth={2.2} fill="none" strokeLinecap="round" />
            ) : (
              <Path d="M3 7.2l2.6 2.6L11 4.4" stroke={colors.white} strokeWidth={2.2} fill="none" strokeLinecap="round" strokeLinejoin="round" />
            )}
          </Svg>
        </View>
        <Text style={styles.text}>{text}</Text>
      </Animated.View>
    </Layer>
  );
}

const styles = StyleSheet.create({
  root: { position: 'absolute', left: 16, right: 16, zIndex: 60, backgroundColor: colors.toastBg, borderRadius: radius.card, paddingVertical: 12, paddingLeft: 14, paddingRight: 12, flexDirection: 'row', alignItems: 'center', gap: 12, ...shadow.toast },
  icon: { width: 24, height: 24, borderRadius: 999, alignItems: 'center', justifyContent: 'center' },
  text: { flex: 1, fontFamily: fam.semibold, fontSize: 14.5, color: colors.white },
});
