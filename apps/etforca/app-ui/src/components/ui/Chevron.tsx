import { Icon } from './Icon';
import { useColors } from '@/theme/theme';

const D = { right: 'chevron-right', down: 'chevron-down', up: 'chevron-up', left: 'chevron-left' } as const;

export function Chevron({ size = 14, color, dir = 'right' }: { size?: number; color?: string; dir?: keyof typeof D }) {
  const colors = useColors();
  return (
    <Icon name={D[dir]} color={color ?? colors.text} size={Math.round(size * 1.15)} strokeWidth={1.9} />
  );
}
