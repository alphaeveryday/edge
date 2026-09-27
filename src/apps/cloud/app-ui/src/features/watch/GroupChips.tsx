import { ScrollView, StyleSheet, View } from 'react-native';
import { Chip } from '@/components/ui';
import { useWatchGroup } from '@/store/watch';
import { useWatchGroups } from './queries';

interface Props {
  onAdd?: () => void;
  onEdit?: () => void;
}

// 관심 그룹 칩 줄. 홈·관심·관심 편집이 공유
export function GroupChips({ onAdd, onEdit }: Props) {
  const { data } = useWatchGroups();
  const { group, setGroup } = useWatchGroup();
  return (
    <View style={styles.row}>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.scroll} style={{ flex: 1 }}>
        {data?.map((g) => <Chip key={g.key} label={g.label} on={g.key === group} onPress={() => setGroup(g.key)} />)}
      </ScrollView>
      {onAdd && <Chip label="그룹" variant="add" onPress={onAdd} />}
      {onEdit && <Chip label="편집" onPress={onEdit} />}
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  scroll: { flexDirection: 'row', gap: 6 },
});
