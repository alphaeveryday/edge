import { useEffect, useMemo, useRef, useState } from 'react';
import { Animated, Easing, PanResponder, useWindowDimensions } from 'react-native';
import type { NativeScrollEvent, NativeSyntheticEvent, PanResponderGestureState } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

const LOW = 0.7;
const HIGH = 0.95;
const CLOSE_DY = 120;
const FLING = 0.5;
const FLING_CLOSE = 2;
const RUBBER = 140;

// 끝 지점 너머의 끌림이 점점 무거워지는 고무줄 저항
const rubber = (over: number) => (1 - 1 / ((over * 0.55) / RUBBER + 1)) * RUBBER;

interface Options {
  open: boolean;
  onClose: () => void;
  tall?: boolean;
  instant?: boolean;
}

// 시트의 보이는 높이 하나로 열기·닫기·끌기와 배경 투명도를 함께 움직임
// tall 시트는 화면 70%에서 시작해 95%까지 확장
export function useSheetDrag({ open, onClose, tall, instant }: Options) {
  const { height: screen } = useWindowDimensions();
  const { top } = useSafeAreaInsets();
  const pos = useRef(new Animated.Value(0)).current;
  const [mounted, setMounted] = useState(open);
  const [fit, setFit] = useState(0);
  const high = tall ? screen - Math.max(screen * (1 - HIGH), top + 8) : fit;
  const low = tall ? screen * LOW : fit;
  const ready = low > 0;
  const now = useRef(0);
  const start = useRef(0);
  const shown = useRef(false);
  // 본문 목록의 맨 위 여부
  const atTop = useRef(true);
  const close = useRef(onClose);
  close.current = onClose;
  const stops = useRef({ low, high });
  stops.current = { low, high };

  useEffect(() => {
    if (open) {
      setMounted(true);
      if (!ready || shown.current) return;
      shown.current = true;
      atTop.current = true;
      if (instant) pos.setValue(low);
      else Animated.spring(pos, { toValue: low, useNativeDriver: false, speed: 14, bounciness: 0 }).start();
    } else if (shown.current) {
      shown.current = false;
      if (instant) {
        pos.setValue(0);
        setMounted(false);
        return;
      }
      Animated.timing(pos, { toValue: 0, duration: 220, easing: Easing.in(Easing.cubic), useNativeDriver: false }).start(({ finished }) => finished && setMounted(false));
    } else {
      setMounted(false);
    }
  }, [open, ready, low, instant, pos]);

  // 내용 높이가 바뀐 fit 시트의 새 높이 맞춤
  useEffect(() => {
    if (!tall && shown.current) Animated.spring(pos, { toValue: low, useNativeDriver: false, speed: 16, bounciness: 0 }).start();
  }, [tall, low, pos]);

  const { handlers, body } = useMemo(() => {
    const snap = (to: number) => Animated.spring(pos, { toValue: to, useNativeDriver: false, speed: 16, bounciness: 6 }).start();
    const vertical = (_: unknown, g: PanResponderGestureState) => Math.abs(g.dy) > 6 && Math.abs(g.dy) > Math.abs(g.dx);
    const move = {
      // 시트가 닫히며 값 리스너가 지워져 위치는 직접 읽음
      onPanResponderGrant: () => {
        pos.stopAnimation((v) => (start.current = now.current = v));
      },
      onPanResponderMove: (_: unknown, g: PanResponderGestureState) => {
        const { high } = stops.current;
        const raw = start.current - g.dy;
        now.current = raw > high ? high + rubber(raw - high) : Math.max(0, raw);
        pos.setValue(now.current);
      },
      onPanResponderRelease: (_: unknown, g: PanResponderGestureState) => {
        const { low, high } = stops.current;
        const cur = now.current;
        if (g.vy > FLING_CLOSE || cur < low - CLOSE_DY || (g.vy > FLING && cur <= low)) close.current();
        else if (g.vy > FLING) snap(low);
        else if (g.vy < -FLING) snap(high);
        else snap(Math.abs(cur - low) <= Math.abs(cur - high) ? low : high);
      },
      onPanResponderTerminate: () => snap(stops.current.low),
    };
    return {
      handlers: PanResponder.create({
        // 빈 곳은 닿을 때부터 제스처 담당
        // 헤더 안 버튼은 세로 이동 시 가로채기
        onStartShouldSetPanResponder: () => true,
        onMoveShouldSetPanResponderCapture: vertical,
        ...move,
      }).panHandlers,
      // 본문은 목록이 맨 위일 때의 아래 방향 끌기만 시트가 넘겨받음
      body: PanResponder.create({
        onMoveShouldSetPanResponderCapture: (e, g) => atTop.current && g.dy > 0 && vertical(e, g),
        onPanResponderTerminationRequest: () => false,
        ...move,
      }).panHandlers,
    };
  }, [pos]);
  const onScroll = useMemo(() => (e: NativeSyntheticEvent<NativeScrollEvent>) => (atTop.current = e.nativeEvent.contentOffset.y <= 0), []);

  const backdrop = { opacity: pos.interpolate({ inputRange: [0, Math.max(low, 1)], outputRange: [0, 1], extrapolate: 'clamp' }) };
  const shift = pos.interpolate({ inputRange: [0, Math.max(low, 1)], outputRange: [Math.max(low, 1), 0], extrapolateLeft: 'clamp', extrapolateRight: tall ? 'clamp' : 'extend' });
  // tall 은 70% 위에서 높이가 늘고 fit 은 시트 전체가 위로 밀림
  const sheet = tall
    ? { height: pos.interpolate({ inputRange: [low, high], outputRange: [low, high], extrapolateLeft: 'clamp', extrapolateRight: 'extend' }), transform: [{ translateY: shift }] }
    : { opacity: ready ? 1 : 0, transform: [{ translateY: shift }] };
  const onLayout = tall ? undefined : (h: number) => setFit(Math.round(h));
  return { mounted, handlers, body, onScroll, backdrop, sheet, onLayout };
}
