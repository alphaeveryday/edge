import { createContext, useContext, useRef, type ReactNode, type Ref } from 'react';
import { Animated, KeyboardAvoidingView, Modal, Pressable, ScrollView, type ScrollViewProps, StyleSheet, View } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { useSheetDrag } from '@/lib/useSheetDrag';
import { createStyles } from '@/theme/theme';
import { radius, shadow } from '@/theme/tokens';
import { useBottomGap } from './BottomBar';

const SheetScroll = createContext<ScrollViewProps['onScroll']>(undefined);

// 맨 위에서 아래로 끌면 시트가 내려가는 시트 안 목록
export function SheetScrollView({ ref, ...props }: ScrollViewProps & { ref?: Ref<ScrollView> }) {
  const onScroll = useContext(SheetScroll);
  return <ScrollView ref={ref} onScroll={onScroll} scrollEventThrottle={16} bounces={false} showsVerticalScrollIndicator={false} {...props} />;
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
}

export function BottomSheet({ open, onClose, children, head, tall, padded = true, instant }: Props) {
  const styles = useStyles();
  const drag = useSheetDrag({ open, onClose, tall, instant });
  // 닫히는 동안의 마지막 내용 유지
  const last = useRef({ children, head });
  if (open) last.current = { children, head };
  return (
    <Modal visible={drag.mounted} transparent animationType="none" onRequestClose={onClose}>
      {/* 탭 화면의 탭바 높이가 섞이지 않는 창 기준 하단 여백 */}
      <SafeAreaProvider>
        <KeyboardAvoidingView behavior="padding" style={styles.root}>
          <Animated.View style={[styles.backdrop, drag.backdrop]}>
            <Pressable style={StyleSheet.absoluteFill} onPress={onClose} />
          </Animated.View>
          <Animated.View
            onLayout={(e) => drag.onLayout?.(e.nativeEvent.layout.height)}
            style={[styles.sheet, !tall && styles.fit, padded && styles.padded, drag.sheet]}
          >
            <View {...drag.handlers} style={[styles.grip, padded && styles.gripPadded, !!head && styles.gripHead]}>
              <View style={[styles.handle, !!head && styles.handleHead]} />
              {last.current.head}
            </View>
            <View {...drag.body} style={tall && styles.body}>
              <SheetScroll.Provider value={drag.onScroll}>{last.current.children}</SheetScroll.Provider>
            </View>
            <SheetGap />
            <View style={styles.under} />
          </Animated.View>
        </KeyboardAvoidingView>
      </SafeAreaProvider>
    </Modal>
  );
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
