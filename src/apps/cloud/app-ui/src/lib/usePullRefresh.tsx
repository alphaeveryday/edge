import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { RefreshControl } from 'react-native';
import { useColors } from '@/theme/theme';

// 띄워 둔 화면의 조회 전부를 다시 받는 당겨서 새로고침
export function usePullRefresh() {
  const colors = useColors();
  const qc = useQueryClient();
  const [refreshing, setRefreshing] = useState(false);
  const onRefresh = async () => {
    setRefreshing(true);
    try {
      await qc.refetchQueries({ type: 'active' });
    } finally {
      setRefreshing(false);
    }
  };
  return <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.textFaint} colors={[colors.primary]} />;
}
