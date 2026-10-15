import type { ColorValue } from 'react-native';
import { Icon } from '@/components/ui/Icon';

type P = { color: ColorValue; size?: number };

export const HomeIcon = ({ color, size = 24 }: P) => <Icon name="house" color={color} size={size * 0.85} />;
export const WatchIcon = ({ color, size = 24 }: P) => <Icon name="heart" color={color} size={size * 0.85} />;
export const ExploreIcon = ({ color, size = 24 }: P) => <Icon name="compass" color={color} size={size * 0.85} />;
export const CommunityIcon = ({ color, size = 24 }: P) => <Icon name="message-circle" color={color} size={size * 0.85} />;
