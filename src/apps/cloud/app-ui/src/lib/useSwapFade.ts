import { useEffect, useRef } from 'react';
import { Animated } from 'react-native';

// 내용 교체의 짧은 흐림·복귀, 새 데이터 대기 중은 흐림 유지
export function useSwapFade(key: string | undefined, waiting: boolean) {
  const op = useRef(new Animated.Value(1)).current;
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    if (waiting) {
      Animated.timing(op, { toValue: 0.5, duration: 120, useNativeDriver: true }).start();
      return;
    }
    op.setValue(0.5);
    Animated.timing(op, { toValue: 1, duration: 200, useNativeDriver: true }).start();
  }, [key, waiting, op]);
  return op;
}
