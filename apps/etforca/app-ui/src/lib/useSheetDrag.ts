import { useEffect, useMemo, useRef, useState } from 'react';
import { useWindowDimensions } from 'react-native';
import { Gesture } from 'react-native-gesture-handler';
import {
  cancelAnimation,
  Easing,
  Extrapolation,
  interpolate,
  runOnJS,
  scrollTo,
  useAnimatedRef,
  useAnimatedScrollHandler,
  useAnimatedStyle,
  useSharedValue,
  withSpring,
  withTiming,
} from 'react-native-reanimated';
import type Animated from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

const LOW = 0.7;
const HIGH = 0.95;
// 시작 높이 아래로 이만큼(화면 높이 비율) 내리면 닫힘
const CLOSE_DY = 0.14;
// 손을 뗀 속도(px/ms)의 기준
const FLING = 0.5;
const RUBBER = 140;
// 버튼·배경 닫힘의 길이이자 끌어 닫기 길이의 상한
const CLOSE_MS = 180;
// 끌어 닫기 길이의 하한
const FLING_MIN_MS = 80;
// 세로 이동으로 판정하는 거리
const SLOP = 6;
// 본문 끌기의 시트·스크롤 판정 거리. 처음 몇 px 의 흔들림 무시
const DECIDE = 20;
// RN Animated.spring 의 speed·bounciness 를 같은 물리 모델의 stiffness·damping 으로 환산한 값
// 열기 14·0, 스냅·fit 높이 맞춤 16·0
// Reanimated 기본 mass 가 4 라 RN 과 같은 1 명시
const OPEN_SPRING = { stiffness: 385, damping: 39, mass: 1, overshootClamping: true };
const SNAP_SPRING = { stiffness: 427, damping: 41, mass: 1, overshootClamping: true };

// 끝 지점 너머의 끌림이 점점 무거워지는 고무줄 저항
const rubber = (over: number) => {
  'worklet';
  return (1 - 1 / ((over * 0.55) / RUBBER + 1)) * RUBBER;
};

interface Options {
  open: boolean;
  onClose: () => void;
  tall?: boolean;
  instant?: boolean;
}

// 시트의 보이는 높이 하나로 열기·닫기·끌기와 배경 투명도를 함께 움직임
// tall 시트는 화면 70%에서 시작해 95%까지 확장
// 본문 끌기는 터치 하나에 시트·스크롤 중 하나. 맨 위에서 아래로 출발하면 시트, 그 밖은 스크롤
export function useSheetDrag({ open, onClose, tall, instant }: Options) {
  const { height: screen } = useWindowDimensions();
  const { top } = useSafeAreaInsets();
  const pos = useSharedValue(0);
  const [mounted, setMounted] = useState(open);
  const [fit, setFit] = useState(0);
  const high = tall ? screen - Math.max(screen * (1 - HIGH), top + 8) : fit;
  const low = tall ? screen * LOW : fit;
  const ready = low > 0;
  const shut = low - screen * CLOSE_DY;
  const stops = useSharedValue({ low, high, shut });
  useEffect(() => { stops.value = { low, high, shut }; }, [stops, low, high, shut]);
  const shown = useRef(false);
  // 닫힘 애니메이션 중 여부
  const closing = useRef(false);
  const close = useRef(onClose);
  close.current = onClose;
  // 본문 목록의 스크롤 위치
  const scrollY = useSharedValue(0);
  const scrollRef = useAnimatedRef<Animated.ScrollView>();
  // 끌기 시작 시점의 높이와 이동량
  const start = useSharedValue(0);
  const startTy = useSharedValue(0);
  // 터치 시작 때 목록이 맨 위였는지와 이번 터치의 본문 끌기 모드(0 미정·1 시트·2 스크롤)
  const atTop = useSharedValue(false);
  const mode = useSharedValue(0);
  // 시트 모드 동안의 목록 스크롤 잠금
  const [locked, setLocked] = useState(false);

  // 처음부터 빠르게 출발해 끝에서 감속하는 닫힘
  const dismiss = useRef((ms: number) => {
    shown.current = false;
    closing.current = true;
    const done = (finished?: boolean) => {
      closing.current = false;
      if (finished) setMounted(false);
    };
    pos.value = withTiming(0, { duration: ms, easing: Easing.out(Easing.quad) }, (finished) => runOnJS(done)(finished));
  }).current;

  useEffect(() => {
    if (open) {
      setMounted(true);
      if (!ready || shown.current) return;
      shown.current = true;
      scrollY.value = 0;
      if (instant) pos.value = low;
      else pos.value = withSpring(low, OPEN_SPRING);
    } else if (shown.current) {
      if (instant) {
        shown.current = false;
        pos.value = 0;
        setMounted(false);
        return;
      }
      dismiss(CLOSE_MS);
    } else if (!closing.current) {
      setMounted(false);
    }
  }, [open, ready, low, instant, pos, scrollY, dismiss]);

  // 내용 높이가 바뀐 fit 시트의 새 높이 맞춤
  useEffect(() => {
    if (!tall && shown.current) pos.value = withSpring(low, SNAP_SPRING);
  }, [tall, low, pos]);

  const { grip, body, native } = useMemo(() => {
    // 손을 뗀 속도(px/s)를 이어받는 스냅
    const snap = (to: number, velocityY = 0) => {
      'worklet';
      pos.value = withSpring(to, { ...SNAP_SPRING, velocity: -velocityY });
    };
    // 손을 뗀 속도(px/s)로 닫기·스냅 결정. 95% 에서 빠르게 내리면 70% 에 멈춤
    const release = (velocityY: number) => {
      'worklet';
      const vy = velocityY / 1000;
      const { low, high, shut } = stops.value;
      const cur = pos.value;
      if (cur < shut || (vy > FLING && cur <= low)) {
        // 손을 뗀 속도로 출발하는 길이. 감속 곡선의 출발 속도는 평균의 두 배
        const ms = vy > 0 ? (2 * cur) / vy : CLOSE_MS;
        runOnJS(dismiss)(Math.min(CLOSE_MS, Math.max(FLING_MIN_MS, ms)));
        runOnJS(close.current)();
      } else if (vy > FLING) snap(low, velocityY);
      else if (vy < -FLING) snap(high, velocityY);
      else snap(Math.abs(cur - low) <= Math.abs(cur - high) ? low : high, velocityY);
    };
    const native = Gesture.Native().shouldCancelWhenOutside(false);
    // 손잡이·헤더: 세로 이동이면 버튼 위에서도 끌기
    const grip = Gesture.Pan()
      .activeOffsetY([-SLOP, SLOP])
      .onStart(() => {
        cancelAnimation(pos);
        start.value = pos.value;
      })
      .onUpdate((e) => {
        const { high } = stops.value;
        const raw = start.value - e.translationY;
        pos.value = raw > high ? high + rubber(raw - high) : Math.max(0, raw);
      })
      .onEnd((e) => release(e.velocityY))
      .onFinalize((_, ok) => { if (!ok) snap(stops.value.low); });
    // 본문: 판정 거리를 넘는 순간의 누적 방향으로 이번 터치의 모드를 정하고 손을 뗄 때까지 유지
    // 시트 모드는 손잡이처럼 시트가 손가락을 따르고 목록은 맨 위에 고정
    // 스크롤 모드는 시트를 건드리지 않아 맨 위 너머로 끌면 목록 위쪽 여백만 드러남
    const body = Gesture.Pan()
      .activeOffsetY([-SLOP, SLOP])
      .simultaneousWithExternalGesture(native)
      .onBegin(() => {
        atTop.value = scrollY.value <= 0;
        mode.value = 0;
      })
      .onUpdate((e) => {
        if (mode.value === 0) {
          // 활성화 직후 이벤트의 이동량이 0 인 경우가 있어 판정 거리는 직접 확인
          if (Math.abs(e.translationY) < DECIDE) return;
          if (!(atTop.value && e.translationY > 0)) {
            mode.value = 2;
            return;
          }
          cancelAnimation(pos);
          mode.value = 1;
          start.value = pos.value;
          startTy.value = e.translationY;
          runOnJS(setLocked)(true);
        }
        if (mode.value !== 1) return;
        const { high } = stops.value;
        const raw = start.value - (e.translationY - startTy.value);
        pos.value = raw > high ? high + rubber(raw - high) : Math.max(0, raw);
        // 스크롤 잠금이 반영되기 전 프레임의 목록 고정
        scrollTo(scrollRef, 0, 0, false);
      })
      .onEnd((e) => {
        if (mode.value === 1) release(e.velocityY);
      })
      .onFinalize((_, ok) => {
        if (mode.value !== 1) return;
        mode.value = 0;
        if (!ok) snap(stops.value.low);
        runOnJS(setLocked)(false);
      });
    // 스크롤 쪽에도 선언해야 스크롤된 상태에서 시작한 끌기가 활성화됨(웹 실측)
    native.simultaneousWithExternalGesture(body);
    return { grip, body, native };
  }, [pos, stops, start, startTy, atTop, mode, scrollY, scrollRef, dismiss]);

  const backdrop = useAnimatedStyle(() => ({
    opacity: interpolate(pos.value, [0, Math.max(stops.value.low, 1)], [0, 1], Extrapolation.CLAMP),
  }));
  // tall 은 70% 위에서 높이가 늘고 fit 은 시트 전체가 위로 밀림
  const sheet = useAnimatedStyle(() => {
    const { low, high } = stops.value;
    const l = Math.max(low, 1);
    const shift = interpolate(pos.value, [0, l], [l, 0], { extrapolateLeft: Extrapolation.CLAMP, extrapolateRight: tall ? Extrapolation.CLAMP : Extrapolation.EXTEND });
    return tall
      ? { height: interpolate(pos.value, [low, high], [low, high], { extrapolateLeft: Extrapolation.CLAMP, extrapolateRight: Extrapolation.EXTEND }), transform: [{ translateY: shift }] }
      : { opacity: ready ? 1 : 0, transform: [{ translateY: shift }] };
  }, [tall, ready]);
  const onLayout = tall ? undefined : (h: number) => setFit(Math.round(h));
  // 시트 안 배경·뒤로가기 닫힘의 즉시 출발
  const closeNow = () => shown.current && !instant && dismiss(CLOSE_MS);
  return { mounted, grip, body, native, scrollRef, scrollY, locked, backdrop, sheet, onLayout, closeNow };
}

// 본문 목록의 스크롤 위치를 시트에 보고하고 통계용 콜백을 JS 에서 호출
export function useSheetScroll(scrollY: { value: number }, onY: (y: number) => void) {
  return useAnimatedScrollHandler({
    onScroll: (e) => {
      scrollY.value = e.contentOffset.y;
      runOnJS(onY)(e.contentOffset.y);
    },
  });
}
