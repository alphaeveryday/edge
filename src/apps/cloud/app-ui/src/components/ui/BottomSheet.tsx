import type { ReactNode } from 'react';
import { Animated, KeyboardAvoidingView, Modal, Pressable, StyleSheet, View } from 'react-native';
import { useSheetDrag } from '@/lib/useSheetDrag';
import { colors, radius, shadow } from '@/theme/tokens';
import { useBottomGap } from './BottomBar';

interface Props {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  padded?: boolean;
}

export function BottomSheet({ open, onClose, children, padded = true }: Props) {
  const gap = useBottomGap();
  const drag = useSheetDrag(open, onClose);
  return (
    <Modal visible={open} transparent animationType="slide" onRequestClose={onClose}>
      <KeyboardAvoidingView behavior="padding" style={styles.root}>
        <Pressable style={styles.backdrop} onPress={onClose} />
        <Animated.View style={[styles.sheet, { paddingBottom: gap }, padded && styles.padded, drag.style]}>
          <View {...drag.handlers} style={[styles.grip, padded && styles.gripPadded]}>
            <View style={styles.handle} />
          </View>
          {children}
        </Animated.View>
      </KeyboardAvoidingView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, justifyContent: 'flex-end' },
  backdrop: { position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: colors.scrim },
  sheet: { backgroundColor: colors.white, borderTopLeftRadius: radius.sheet, borderTopRightRadius: radius.sheet, maxHeight: '78%', ...shadow.sheet },
  padded: { paddingHorizontal: 20 },
  grip: { paddingTop: 10, paddingBottom: 18 },
  gripPadded: { marginHorizontal: -20 },
  handle: { width: 38, height: 4, borderRadius: radius.pill, backgroundColor: colors.line, alignSelf: 'center' },
});
