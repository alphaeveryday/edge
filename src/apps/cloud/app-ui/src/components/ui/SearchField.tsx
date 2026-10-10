import { TextInput, View } from 'react-native';
import { Icon } from './Icon';
import { createStyles, useColors } from '@/theme/theme';
import { radius } from '@/theme/tokens';
import { fam } from '@/theme/typography';

interface Props {
  value: string;
  onChangeText: (v: string) => void;
  placeholder?: string;
  autoFocus?: boolean;
}

export function SearchField({ value, onChangeText, placeholder = '검색', autoFocus }: Props) {
  const styles = useStyles();
  const colors = useColors();
  return (
    <View style={styles.root}>
      <Icon name="search" color={colors.textFaint} size={18} />
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

const useStyles = createStyles((colors) => ({
  root: { height: 44, borderRadius: radius.field, backgroundColor: colors.surface, flexDirection: 'row', alignItems: 'center', gap: 10, paddingHorizontal: 14 },
  input: { flex: 1, alignSelf: 'stretch', fontFamily: fam.regular, fontSize: 15, color: colors.text, padding: 0 },
}));
