import { useMemo, useState } from 'react';
import { ScrollView, Text, View } from 'react-native';
import type { EtfSummary } from '@/api';
import { SearchField, SectorIcon } from '@/components/ui';
import { createStyles } from '@/theme/theme';
import { fam } from '@/theme/typography';
import { PickCircle } from './PickCircle';

interface Props {
  etfs: EtfSummary[];
  picked: string[];
  onToggle: (code: string) => void;
}

// 검색과 ETF 원형 선택 그리드
export function EtfPickGrid({ etfs, picked, onToggle }: Props) {
  const styles = useStyles();
  const [q, setQ] = useState('');
  const list = useMemo(() => {
    const k = q.trim();
    return k ? etfs.filter((e) => e.name.includes(k) || e.theme.includes(k)) : etfs;
  }, [etfs, q]);
  return (
    <>
      <View style={styles.search}>
        <SearchField value={q} onChangeText={setQ} placeholder="ETF·테마 검색" />
      </View>
      <ScrollView contentContainerStyle={styles.list} showsVerticalScrollIndicator={false} keyboardShouldPersistTaps="handled">
        <View style={styles.grid}>
          {list.map((e) => (
            <View key={e.code} style={styles.cell}>
              <PickCircle size={84} label={e.name} on={picked.includes(e.code)} hot={e.hot} onPress={() => onToggle(e.code)}>
                <SectorIcon theme={e.theme} bg={e.logoBg} size={78} />
              </PickCircle>
            </View>
          ))}
        </View>
        {list.length === 0 && <Text style={styles.empty}>찾는 ETF가 없어요</Text>}
      </ScrollView>
    </>
  );
}

const useStyles = createStyles((colors) => ({
  search: { paddingTop: 12, paddingHorizontal: 20 },
  list: { paddingTop: 22, paddingHorizontal: 16, paddingBottom: 24 },
  grid: { flexDirection: 'row', flexWrap: 'wrap', rowGap: 22 },
  cell: { width: '33.33%', alignItems: 'center' },
  empty: { textAlign: 'center', fontFamily: fam.regular, fontSize: 14, color: colors.textMuted, paddingVertical: 40 },
}));
