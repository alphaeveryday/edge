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
  useAnimatedReaction,
  useAnimatedRef,
  useAnimatedScrollHandler,
  useAnimatedStyle,
  useSharedValue,
  withSequence,
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
// iOS 스크롤 기본 감속(UIScrollView.DecelerationRate.normal, ms 당 속도 비율)
const DECEL = 0.998;
// RN Animated.spring 의 speed·bounciness 를 같은 물리 모델의 stiffness·damping 으로 환산한 값
// 열기 14·0, 스냅·fit 높이 맞춤 16·0
// Reanimated 기본 mass 가 4 라 RN 과 같은 1 명시
const OPEN_SPRING = { stiffness: 385, damping: 39, mass: 1, overshootClamping: true };
const SNAP_SPRING = { stiffness: 427, damping: 41, mass: 1, overshootClamping: true };

// 직접 스크롤의 경계 밖 저항. UIScrollView 와 같은 곡선(계수 0.55, 기준 길이=본문 높이)
const overscroll = (x: number, d: number) => {
  'worklet';
  const h = Math.max(d, 1);
  return (1 - 1 / ((x * 0.55) / h + 1)) * h;
};
// 경계 밖에서 경계로 돌아오는 튕김. 임계 감쇠라 넘침 없이 복귀
const BOUNCE = { stiffness: 100, damping: 20, mass: 1 };
// 지수 감속 구간의 진행률. 길이 전체의 감속 지수 a 에 맞춘 0~1
const decayEase = (a: number) => (t: number) => {
  'worklet';
  return (1 - Math.exp(-a * t)) / (1 - Math.exp(-a));
};

// 손을 뗀 위치와 속도(px/s)에서 이어지는 iOS 스크롤과 같은 움직임과 최종 정지 위치
// 경계 안은 ms 당 0.998 지수 감속. 경계에 닿으면 그 순간 속도로 경계 밖에 넘었다 튕겨 돌아옴
// 경계 밖에서 놓으면 놓은 속도로 경계에 튕겨 돌아옴
// Reanimated withDecay 는 프레임마다 감속이 누적돼 끝에서 급정지하므로 직접 계산
const fling = (from: number, velocity: number, max: number, done: () => void) => {
  'worklet';
  const k = -Math.log(DECEL);
  const v = velocity / 1000;
  if (from < 0 || from > max) {
    const edge = from < 0 ? 0 : max;
    return { anim: withSpring(edge, { ...BOUNCE, velocity }, done), end: edge };
  }
  if (Math.abs(v) < 0.05) return null;
  const reach = v / k;
  const edge = v > 0 ? max : 0;
  const gap = edge - from;
  if (Math.abs(reach) < Math.abs(gap)) {
    // 속도가 0.02px/ms 아래로 떨어지는 시점까지
    const ms = Math.log(Math.abs(v) / 0.02) / k;
    const a = k * ms;
    const to = from + reach * (1 - Math.exp(-a));
    return { anim: withTiming(to, { duration: ms, easing: decayEase(a) }, done), end: to };
  }
  const hit = v * (1 - gap / reach);
  const bounce = withSpring(edge, { ...BOUNCE, velocity: hit * 1000 }, done);
  if (Math.abs(gap) < 0.5) return { anim: bounce, end: edge };
  const ms = -Math.log(1 - gap / reach) / k;
  return { anim: withSequence(withTiming(edge, { duration: ms, easing: decayEase(k * ms) }), bounce), end: edge };
};

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
// 본문이 최상단에서 멈춰 있으면 네이티브 스크롤을 잠가 본문 끌기가 터치를 모두 받음
// 그 상태에서 아래로 끌면 시트, 위로 밀면 그 터치만 직접 스크롤하고 손을 떼면 잠금 해제
// iOS 에서 Gesture Handler 의 스크롤 제스처 관계 설정(동시 인식·대기)이 적용되지 않아 먼저 활성화된 쪽이 터치를 가져가기 때문
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
  // 본문의 스크롤 위치와 본문 크기(최대 스크롤 위치·보이는 높이)
  const scrollY = useSharedValue(0);
  const box = useSharedValue({ max: 0, view: 0 });
  const scrollRef = useAnimatedRef<Animated.ScrollView>();
  // 최상단에서 멈춘 상태의 잠금. 네이티브 스크롤을 끄고 본문 끌기를 켬
  const [locked, setLocked] = useState(true);
  const lockedSV = useSharedValue(true);
  // 네이티브 스크롤 터치 중 여부
  const touching = useSharedValue(false);
  // 직접 스크롤 위치와 그 관성 진행 여부
  const manualY = useSharedValue(0);
  const driving = useSharedValue(false);
  // 끌기 시작 시점의 높이와 이동량
  const start = useSharedValue(0);
  const startTy = useSharedValue(0);
  // 본문 끌기 모드(0 첫 이동 전·1 시트·2 직접 스크롤)
  const mode = useSharedValue(0);
  // 터치·관성 없이 최상단에 멈추면 잠금
  useAnimatedReaction(
    () => !lockedSV.value && !touching.value && !driving.value && Math.abs(scrollY.value) < 0.5,
    (idle, prev) => {
      if (idle && !prev) {
        lockedSV.value = true;
        runOnJS(setLocked)(true);
      }
    },
  );
  // 잠금 중 본문 끌기 밖의 이유로 스크롤 위치가 생기면 해제
  // 다른 화면에 다녀오거나 접기로 내용이 바뀌어 최상단이 아닌 채로 다시 열린 경우
  useAnimatedReaction(
    () => lockedSV.value && mode.value === 0 && !driving.value && Math.abs(scrollY.value) >= 0.5,
    (moved, prev) => {
      if (moved && !prev) {
        lockedSV.value = false;
        runOnJS(setLocked)(false);
      }
    },
  );
  // 직접 스크롤의 본문 반영
  useAnimatedReaction(() => manualY.value, (y) => { if (driving.value || mode.value === 2) scrollTo(scrollRef, 0, y, false); });

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
      lockedSV.value = true;
      setLocked(true);
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

  const { grip, native, release, snap } = useMemo(() => {
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
    const native = Gesture.Native().shouldCancelWhenOutside(false)
      .onBegin(() => {
        touching.value = true;
        // 직접 스크롤의 관성 중 새 터치는 네이티브 스크롤이 이어받음
        if (driving.value) {
          cancelAnimation(manualY);
          driving.value = false;
        }
      })
      .onFinalize(() => {
        touching.value = false;
      });
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
    return { grip, native, release, snap };
  }, [pos, stops, start, touching, driving, manualY, dismiss]);

  // 본문: 잠금 상태에서만 켜짐. 활성화 뒤 첫 이동이 아래면 시트, 위면 직접 스크롤
  const body = useMemo(
    () =>
      Gesture.Pan()
        .enabled(locked)
        .activeOffsetY([-SLOP, SLOP])
        .onStart((e) => {
          mode.value = 0;
          // 직접 스크롤의 튕김 중 새 터치는 그 위치에서 이어받고, 아니면 현재 스크롤 위치에서 출발
          if (driving.value) {
            cancelAnimation(manualY);
            driving.value = false;
          } else manualY.value = scrollY.value;
          // iOS 는 활성화 순간 이동량이 0 으로 되돌아가고 웹은 활성화 거리가 남음
          startTy.value = e.translationY;
        })
        .onUpdate((e) => {
          const dy = e.translationY - startTy.value;
          if (mode.value === 0) {
            if (dy === 0) return;
            if (dy > 0 && manualY.value >= 0) {
              cancelAnimation(pos);
              start.value = pos.value;
              mode.value = 1;
            } else {
              start.value = manualY.value;
              mode.value = 2;
            }
          }
          if (mode.value === 1) {
            const { high } = stops.value;
            const raw = start.value - dy;
            pos.value = raw > high ? high + rubber(raw - high) : Math.max(0, raw);
          } else {
            const { max, view } = box.value;
            const raw = start.value - dy;
            manualY.value = raw < 0 ? -overscroll(-raw, view) : raw > max ? max + overscroll(raw - max, view) : raw;
          }
        })
        .onEnd((e) => {
          if (mode.value === 1) release(e.velocityY);
          else if (mode.value === 2) {
            // 손을 뗀 속도로 이어지는 관성·튕김. 최상단이 아닌 곳에 멈추면 잠금을 풀어 다음 터치는 네이티브 스크롤
            const f = fling(manualY.value, -e.velocityY, box.value.max, () => {
              'worklet';
              driving.value = false;
            });
            if (f) {
              driving.value = true;
              manualY.value = f.anim;
            }
            if ((f ? f.end : manualY.value) > 0.5) {
              lockedSV.value = false;
              runOnJS(setLocked)(false);
            }
          }
        })
        .onFinalize((_, ok) => {
          if (mode.value === 1 && !ok) snap(stops.value.low);
          mode.value = 0;
        }),
    [locked, release, snap, pos, stops, start, startTy, mode, box, scrollY, manualY, driving, lockedSV],
  );

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
  return { mounted, grip, body, native, scrollY, box, scrollRef, locked, backdrop, sheet, onLayout, closeNow };
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
