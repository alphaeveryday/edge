import { Fragment } from 'react';
import { Platform, StyleSheet, Text, View } from 'react-native';
import { FullWindowOverlay } from 'react-native-screens';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import Svg, { Path } from 'react-native-svg';
import { useToast } from '@/store/toast';
import { colors, shadow } from '@/theme/tokens';
import { fam } from '@/theme/typography';

// 전체 화면 모달과 시트 위에도 보이도록 iOS 는 창 최상단 레이어에 표시
const Layer = Platform.OS === 'ios' ? FullWindowOverlay : Fragment;

export function Toast() {
  const text = useToast((s) => s.text);
  const kind = useToast((s) => s.kind);
  const { top } = useSafeAreaInsets();
  if (!text) return null;
  const error = kind === 'error';
  return (
    <Layer>
      <View style={[styles.root, { top: top + 8 }]} pointerEvents="none">
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
      </View>
    </Layer>
  );
}

const styles = StyleSheet.create({
  root: { position: 'absolute', left: 16, right: 16, zIndex: 60, backgroundColor: colors.toastBg, borderRadius: 16, paddingVertical: 12, paddingLeft: 14, paddingRight: 12, flexDirection: 'row', alignItems: 'center', gap: 12, ...shadow.toast },
  icon: { width: 24, height: 24, borderRadius: 999, alignItems: 'center', justifyContent: 'center' },
  text: { flex: 1, fontFamily: fam.semibold, fontSize: 14.5, color: colors.white },
});
