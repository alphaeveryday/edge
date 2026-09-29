import { Redirect } from 'expo-router';
import { useSession } from '@/store/session';

export default function Index() {
  const onboarded = useSession((s) => s.onboarded);
  const restored = useSession((s) => s.restored);
  if (!restored) return null;
  return <Redirect href={onboarded ? '/(tabs)/home' : '/onboarding/how'} />;
}
