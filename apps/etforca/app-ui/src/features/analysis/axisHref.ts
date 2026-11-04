import type { Href } from 'expo-router';
import type { Axis } from '@/api';

// 수치 지표가 없는 이슈 축의 요인 상세 직행
export const axisHref = (code: string, axis: Axis | string, date?: string): Href => ({
  pathname: axis === '이슈' ? '/factor/[code]/[axis]' : '/metric/[code]/[axis]',
  params: { code, axis, ...(date ? { date } : {}) },
});
