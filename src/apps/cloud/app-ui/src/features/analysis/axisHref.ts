import type { Href } from 'expo-router';
import type { Axis } from '@/api';

// 수치 지표가 없는 이슈 축은 요인 상세로 바로
export const axisHref = (code: string, axis: Axis | string): Href => (axis === '이슈' ? `/factor/${code}/${axis}` : `/metric/${code}/${axis}`);
