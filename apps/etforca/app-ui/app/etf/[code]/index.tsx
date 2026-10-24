import { Redirect, useLocalSearchParams } from 'expo-router';

export default function EtfIndex() {
  const { code } = useLocalSearchParams<{ code: string }>();
  return <Redirect href={`/etf/${code}/brief`} />;
}
