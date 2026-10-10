import { Pressable, Text, View } from 'react-native';
import { Icon, type IconName } from './Icon';
import { Chevron } from './Chevron';
import { createStyles, useColors } from '@/theme/theme';
import { fam, type } from '@/theme/typography';

export type ListIcon = 'chart' | 'star' | 'search' | 'comm' | 'bell' | 'moon' | 'bars';
const GLYPH: Record<ListIcon, IconName> = { chart: 'trending-up', star: 'star', search: 'search', comm: 'message-circle', bell: 'bell', moon: 'moon', bars: 'chart-no-axes-column' };
export function RowIcon({ name }: { name: ListIcon }) {
  return <Icon name={GLYPH[name]} color={useColors().textSub} size={19} />;
}

interface Props {
  label: string;
  icon?: ListIcon;
  sub?: string;
  value?: string;
  labelColor?: string;
  chevron?: boolean;
  divider?: boolean;
  onPress?: () => void;
}

export function ListRow({ label, icon, sub, value, labelColor, chevron = true, divider, onPress }: Props) {
  const styles = useStyles();
  const colors = useColors();
  return (
    <Pressable onPress={onPress} style={({ pressed }) => [styles.row, divider && styles.divider, pressed && { opacity: 0.6 }]}>
      {icon && <View style={styles.icon}><RowIcon name={icon} /></View>}
      <View style={{ flex: 1, gap: 3 }}>
        <Text numberOfLines={1} style={[styles.label, { color: labelColor ?? colors.text }]}>{label}</Text>
        {!!sub && <Text numberOfLines={1} style={styles.sub}>{sub}</Text>}
      </View>
      {!!value && <Text style={styles.value}>{value}</Text>}
      {chevron && <Chevron size={16} color={colors.textDisabled} />}
    </Pressable>
  );
}

const useStyles = createStyles((colors) => ({
  row: { flexDirection: 'row', alignItems: 'center', gap: 14, paddingVertical: 14, paddingHorizontal: 8 },
  divider: { borderBottomWidth: 1, borderBottomColor: colors.surface },
  icon: { width: 22, height: 22, alignItems: 'center', justifyContent: 'center' },
  label: type.listLabel,
  sub: { ...type.caption, color: colors.textMuted },
  value: { fontFamily: fam.regular, fontSize: 14, color: colors.textMuted },
}));
