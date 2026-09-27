import { useLocalSearchParams } from 'expo-router';
import { Placeholder } from '@/components/Placeholder';

export default function ThemeDetail() {
  const { id } = useLocalSearchParams<{ id: string }>();
  return <Placeholder title={`테마 분석 · ${id}`} />;
}
