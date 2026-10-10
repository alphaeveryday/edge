import { useQueryClient } from '@tanstack/react-query';
import * as Haptics from 'expo-haptics';
import { useEffect, useRef, useState } from 'react';
import { Animated, Easing, Platform, RefreshControl, StyleSheet, type NativeScrollEvent, type NativeSyntheticEvent } from 'react-native';
import Svg, { Circle } from 'react-native-svg';
import { useColors } from '@/theme/theme';

type OnScroll = (e: NativeSyntheticEvent<NativeScrollEvent>) => void;

const AnimatedCircle = Animated.createAnimatedComponent(Circle);
const SIZE = 26;
const R = 10;
const C = 2 * Math.PI * R;
// 원호가 다 차는 당김 거리, 기본 새로고침 판정 거리 기준
const FULL = 110;
// 새로고침 중 기본 컨트롤이 잡아 두는 상단 여백
const HOLD = 60;
// 빠른 응답에도 새로고침이 보이는 최소 시간
const MIN_MS = 800;
const IOS = Platform.OS === 'ios';

// 띄워 둔 화면의 조회 전부를 다시 받는 당겨서 새로고침
// iOS 는 기본 스피너를 숨기고 당긴 만큼 차오르는 원호 표시
export function usePullRefresh(more?: { onScroll: OnScroll }) {
  const colors = useColors();
  const qc = useQueryClient();
  const [refreshing, setRefreshing] = useState(false);
  const pull = useRef(new Animated.Value(0)).current;
  const spin = useRef(new Animated.Value(0)).current;
  const full = useRef(false);
  useEffect(() => {
    if (!refreshing) return;
    spin.setValue(0);
    const loop = Animated.loop(Animated.timing(spin, { toValue: 1, duration: 800, easing: Easing.linear, useNativeDriver: true }));
    loop.start();
    return () => loop.stop();
  }, [refreshing, spin]);
  const onRefresh = async () => {
    // Android 는 당긴 거리를 못 받아 새로고침 판정 순간의 진동
    if (Platform.OS === 'android') Haptics.performAndroidHapticsAsync(Haptics.AndroidHaptics.Gesture_End);
    setRefreshing(true);
    const at = Date.now();
    try {
      await qc.refetchQueries({ type: 'active' });
    } finally {
      await new Promise((ok) => setTimeout(ok, Math.max(0, MIN_MS - (Date.now() - at))));
      setRefreshing(false);
    }
  };
  const onScroll: OnScroll = (e) => {
    if (IOS) {
      const y = -e.nativeEvent.contentOffset.y;
      pull.setValue(y);
      // 원호가 다 차는 순간의 진동, 되돌렸다 다시 넘을 때만 반복
      if (y >= FULL && !full.current) {
        full.current = true;
        Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
      } else if (y < FULL) full.current = false;
    }
    more?.onScroll(e);
  };
  const refreshControl = (
    <RefreshControl
      refreshing={refreshing}
      onRefresh={onRefresh}
      tintColor={IOS ? 'transparent' : colors.textFaint}
      colors={[colors.primary]}
    />
  );
  const indicator = IOS && (
    <Animated.View
      pointerEvents="none"
      style={[styles.wrap, { opacity: refreshing ? 1 : pull.interpolate({ inputRange: [16, 40], outputRange: [0, 1], extrapolate: 'clamp' }) }]}
    >
      <Animated.View style={refreshing && { transform: [{ rotate: spin.interpolate({ inputRange: [0, 1], outputRange: ['0deg', '360deg'] }) }] }}>
        <Svg width={SIZE} height={SIZE} viewBox={`0 0 ${SIZE} ${SIZE}`}>
          <Circle cx={SIZE / 2} cy={SIZE / 2} r={R} stroke={colors.line} strokeWidth={2.5} fill="none" />
          <AnimatedCircle
            cx={SIZE / 2}
            cy={SIZE / 2}
            r={R}
            stroke={colors.primary}
            strokeWidth={2.5}
            strokeLinecap="round"
            fill="none"
            strokeDasharray={`${C} ${C}`}
            strokeDashoffset={refreshing ? C * 0.72 : pull.interpolate({ inputRange: [40, FULL], outputRange: [C, 0], extrapolate: 'clamp' })}
            transform={`rotate(-90 ${SIZE / 2} ${SIZE / 2})`}
          />
        </Svg>
      </Animated.View>
    </Animated.View>
  );
  // iOS 관성 스크롤 중 스크롤뷰가 다른 터치를 빼앗는 응답자 재협상 차단
  return { scroll: { refreshControl, onScroll, scrollEventThrottle: 16, disableScrollViewPanResponder: true }, indicator };
}

// 스크롤 내용 맨 위 바깥, 당겨서 드러나는 자리
const styles = StyleSheet.create({
  wrap: { position: 'absolute', left: 0, right: 0, top: -(HOLD + SIZE) / 2, alignItems: 'center' },
});
