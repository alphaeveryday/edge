import { StyleSheet, TextInput, View } from 'react-native';
import Svg, { Circle, Path } from 'react-native-svg';
import { colors, radius } from '@/theme/tokens';
import { fam } from '@/theme/typography';

interface Props {
  value: string;
  onChangeText: (v: string) => void;
  placeholder?: string;
  autoFocus?: boolean;
}

export function SearchField({ value, onChangeText, placeholder = '검색', autoFocus }: Props) {
  return (
    <View style={styles.root}>
      <Svg width={18} height={18} viewBox="0 0 18 18">
        <Circle cx={8} cy={8} r={5.5} stroke={colors.textFaint} strokeWidth={1.8} fill="none" />
        <Path d="M12.5 12.5L16 16" stroke={colors.textFaint} strokeWidth={1.8} strokeLinecap="round" />
      </Svg>
      <TextInput
        value={value}
        onChangeText={onChangeText}
        placeholder={placeholder}
        placeholderTextColor={colors.textFaint}
        autoFocus={autoFocus}
        autoCorrect={false}
        style={styles.input}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  root: { height: 44, borderRadius: radius.field, backgroundColor: colors.surface, flexDirection: 'row', alignItems: 'center', gap: 10, paddingHorizontal: 14 },
  input: { flex: 1, alignSelf: 'stretch', fontFamily: fam.regular, fontSize: 15, color: colors.text, padding: 0 },
});
