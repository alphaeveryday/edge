import { createContext, useContext, useEffect, useRef, type ReactNode, type Ref } from 'react';
import { KeyboardAvoidingView, Modal, Pressable, type ScrollView, type ScrollViewProps, StyleSheet, View } from 'react-native';
import { Gesture, GestureDetector, GestureHandlerRootView, type NativeGesture } from 'react-native-gesture-handler';
import Animated, { type AnimatedRef } from 'react-native-reanimated';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { useSheetDrag, useSheetScroll } from '@/lib/useSheetDrag';
import { createStyles } from '@/theme/theme';
import { radius, shadow } from '@/theme/tokens';
import { useBottomGap } from './BottomBar';

interface SheetScrollCtx {
  native: NativeGesture;
  scrollRef?: AnimatedRef<Animated.ScrollView>;
  scrollY: { value: number };
  onSeen: (pct: number) => void;
}
const SheetScroll = createContext<SheetScrollCtx>({ native: Gesture.Native(), scrollY: { value: 0 }, onSeen: () => {} });

// 맨 위에서 아래로 끌면 시트가 내려가는 시트 안 목록. 스크롤하다 맨 위에 닿은 뒤의 끌어내림도 이어서 시트로 넘어감
// 본 만큼의 비율을 시트에 보고
export function SheetScrollView({ ref, ...props }: ScrollViewProps & { ref?: Ref<ScrollView> }) {
  const { native, scrollRef, scrollY, onSeen } = useContext(SheetScroll);
  const m = useRef({ y: 0, view: 0, content: 0 });
  const report = () => m.current.content > 0 && m.current.view > 0 && onSeen(Math.min(100, Math.round(((m.current.y + m.current.view) / m.current.content) * 100)));
  const onScroll = useSheetScroll(scrollY, (y) => { m.current.y = y; report(); });
  // 시트의 애니메이션 ref 와 호출부 ref 를 함께 연결
  const setRef = (r: Animated.ScrollView | null) => {
    scrollRef?.(r);
    if (typeof ref === 'function') ref(r as unknown as ScrollView);
    else if (ref) ref.current = r as unknown as ScrollView;
  };
  return (
    <GestureDetector gesture={native}>
      <Animated.ScrollView
        ref={setRef}
        onScroll={onScroll}
        onLayout={(e) => { m.current.view = e.nativeEvent.layout.height; report(); }}
        onContentSizeChange={(_, h) => { m.current.content = h; report(); }}
        scrollEventThrottle={16}
        bounces={false}
        overScrollMode="never"
        showsVerticalScrollIndicator={false}
        {...props}
      />
    </GestureDetector>
  );
}

export type CloseMethod = 'drag' | 'backdrop' | 'back' | 'next' | 'button' | 'navigate';
export interface SheetStats {
  session?: string;
  duration_sec: number;
  scroll_pct: number;
  close_method: CloseMethod;
}

interface Props {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  // 손잡이와 함께 끌기 영역이 되는 상단
  head?: ReactNode;
  // 화면 70% 기본에 95%까지 확장
  tall?: boolean;
  padded?: boolean;
  // 애니메이션 없는 표시
  instant?: boolean;
  // 열림 한 번의 체류·스크롤·닫은 방법 보고
  // 열린 채 session 이 바뀌면 next 로 닫힘
  // 시트 밖에서 닫으면 closeMethod
  onClosed?: (s: SheetStats) => void;
  session?: string;
  closeMethod?: CloseMethod;
}

export function BottomSheet({ open, onClose, children, head, tall, padded = true, instant, onClosed, session, closeMethod = 'button' }: Props) {
  const styles = useStyles();
  const stats = useSheetStats({ open, onClosed, session, closeMethod });
  const close = (m: CloseMethod) => { stats.method.current = m; onClose(); };
  const drag = useSheetDrag({ open, onClose: () => close('drag'), tall, instant });
  const closeNow = (m: CloseMethod) => { drag.closeNow(); close(m); };
  // 닫히는 동안의 마지막 내용 유지
  const last = useRef({ children, head });
  if (open) last.current = { children, head };
  return (
    <Modal visible={drag.mounted} transparent animationType="none" onRequestClose={() => closeNow('back')}>
      {/* 탭 화면의 탭바 높이가 섞이지 않는 창 기준 하단 여백 */}
      {/* 모달은 별도 창이라 제스처 루트가 다시 필요 */}
      <GestureHandlerRootView style={styles.root}>
        <SafeAreaProvider>
          <KeyboardAvoidingView behavior="padding" style={styles.root}>
            <Animated.View style={[styles.backdrop, drag.backdrop]}>
              <Pressable style={StyleSheet.absoluteFill} onPress={() => closeNow('backdrop')} />
            </Animated.View>
            <Animated.View
              onLayout={(e) => drag.onLayout?.(e.nativeEvent.layout.height)}
              style={[styles.sheet, !tall && styles.fit, padded && styles.padded, drag.sheet]}
            >
              <GestureDetector gesture={drag.grip}>
                <View style={[styles.grip, padded && styles.gripPadded, !!head && styles.gripHead]}>
                  <View style={[styles.handle, !!head && styles.handleHead]} />
                  {last.current.head}
                </View>
              </GestureDetector>
              <GestureDetector gesture={drag.body}>
                <View style={tall && styles.body}>
                  <SheetScroll.Provider value={{ native: drag.native, scrollRef: drag.scrollRef, scrollY: drag.scrollY, onSeen: stats.onSeen }}>{last.current.children}</SheetScroll.Provider>
                </View>
              </GestureDetector>
              <SheetGap />
              <View style={styles.under} />
            </Animated.View>
          </KeyboardAvoidingView>
        </SafeAreaProvider>
      </GestureHandlerRootView>
    </Modal>
  );
}

function useSheetStats({ open, onClosed, session, closeMethod }: { open: boolean; onClosed?: (s: SheetStats) => void; session?: string; closeMethod: CloseMethod }) {
  const method = useRef<CloseMethod | null>(null);
  const cur = useRef<{ session?: string; at: number; pct: number } | null>(null);
  const report = useRef(onClosed);
  report.current = onClosed;
  const fallback = useRef(closeMethod);
  fallback.current = closeMethod;
  useEffect(() => {
    const end = (m: CloseMethod) => {
      const c = cur.current;
      if (!c) return;
      cur.current = null;
      report.current?.({ session: c.session, duration_sec: Math.round((Date.now() - c.at) / 1000), scroll_pct: c.pct, close_method: m });
    };
    if (!open) {
      end(method.current ?? fallback.current);
      method.current = null;
      return;
    }
    if (cur.current && cur.current.session !== session) end('next');
    if (!cur.current) cur.current = { session, at: Date.now(), pct: 0 };
  }, [open, session]);
  const onSeen = (pct: number) => { if (cur.current) cur.current.pct = Math.max(cur.current.pct, pct); };
  return { method, onSeen };
}

function SheetGap() {
  return <View style={{ height: useBottomGap() }} />;
}

const useStyles = createStyles((colors) => ({
  root: { flex: 1, justifyContent: 'flex-end' },
  backdrop: { position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: colors.scrim },
  sheet: { backgroundColor: colors.bg, borderTopLeftRadius: radius.sheet, borderTopRightRadius: radius.sheet, ...shadow.sheet },
  fit: { maxHeight: '90%' },
  body: { flex: 1 },
  padded: { paddingHorizontal: 20 },
  grip: { paddingTop: 10, paddingBottom: 18 },
  gripPadded: { marginHorizontal: -20, paddingHorizontal: 20 },
  gripHead: { paddingBottom: 12 },
  handle: { width: 38, height: 4, borderRadius: radius.pill, backgroundColor: colors.line, alignSelf: 'center' },
  handleHead: { marginBottom: 18 },
  // 위로 당겨 올린 만큼 드러나는 시트 아래 빈자리 채움
  under: { position: 'absolute', left: 0, right: 0, top: '100%', height: 400, backgroundColor: colors.bg },
}));
