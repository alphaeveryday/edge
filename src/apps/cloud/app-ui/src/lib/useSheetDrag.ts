import { useEffect, useMemo, useRef } from 'react';
import { Animated, PanResponder } from 'react-native';
import type { PanResponderGestureState } from 'react-native';

const CLOSE_DY = 120;
const CLOSE_VY = 1;

// 그래버와 시트 상단을 끌어 내려 닫기
export function useSheetDrag(open: boolean, onClose: () => void) {
  const y = useRef(new Animated.Value(0)).current;
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    if (open) y.setValue(0);
  }, [open, y]);
  const handlers = useMemo(() => {
    const back = () => Animated.spring(y, { toValue: 0, useNativeDriver: true, bounciness: 0 }).start();
    const vertical = (_: unknown, g: PanResponderGestureState) => Math.abs(g.dy) > 6 && Math.abs(g.dy) > Math.abs(g.dx);
    return PanResponder.create({
      // 빈 곳은 닿을 때부터 제스처 담당
      // 헤더 안 버튼은 세로 이동 시 가로채기
      onStartShouldSetPanResponder: () => true,
      onMoveShouldSetPanResponderCapture: vertical,
      onPanResponderMove: (_, g) => y.setValue(Math.max(0, g.dy)),
      onPanResponderRelease: (_, g) => {
        if (g.dy > CLOSE_DY || (g.dy > 20 && g.vy > CLOSE_VY)) close.current();
        else back();
      },
      onPanResponderTerminate: back,
    }).panHandlers;
  }, [y]);
  return { style: { transform: [{ translateY: y }] }, handlers };
}
