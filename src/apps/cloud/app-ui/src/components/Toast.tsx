import { StyleSheet, Text, View } from 'react-native';
import Svg, { Path } from 'react-native-svg';
import { useToast } from '@/store/toast';
import { fam } from '@/theme/typography';

export function Toast() {
  const text = useToast((s) => s.text);
  if (!text) return null;
  return (
    <View style={styles.root} pointerEvents="none">
      <View style={styles.check}>
        <Svg width={13} height={13} viewBox="0 0 14 14">
          <Path d="M3 7.2l2.6 2.6L11 4.4" stroke="#FFFFFF" strokeWidth={2.2} fill="none" strokeLinecap="round" strokeLinejoin="round" />
        </Svg>
      </View>
      <Text style={styles.text}>{text}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { position: 'absolute', left: 16, right: 16, bottom: 96, zIndex: 60, backgroundColor: '#2B2F3A', borderRadius: 16, paddingVertical: 12, paddingLeft: 14, paddingRight: 12, flexDirection: 'row', alignItems: 'center', gap: 12, shadowColor: '#000', shadowOpacity: 0.28, shadowRadius: 15, shadowOffset: { width: 0, height: 10 }, elevation: 8 },
  check: { width: 24, height: 24, borderRadius: 999, backgroundColor: '#22C55E', alignItems: 'center', justifyContent: 'center' },
  text: { flex: 1, fontFamily: fam.semibold, fontSize: 14.5, color: '#FFFFFF' },
});
