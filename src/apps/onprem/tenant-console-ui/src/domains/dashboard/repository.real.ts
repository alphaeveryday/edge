/* dashboard 도메인 — real 구현 (tenant-console-api 연동)
 *
 * 현재는 stub 이다. 백엔드 dashboard 엔드포인트가 완성되면 config/dataSources.ts 의
 * dashboard 를 'real' 로 바꾸는 것만으로 이 구현이 활성화된다. 응답 DTO 가 도메인
 * 타입과 다르면 여기서 매핑한다 (mock·real 의 반환 타입은 동일해야 한다).
 */
import { apiClient } from '../../api/client';
import type { DashboardRepository } from './repository';
import type { DashboardOverview } from './types';

export const realDashboardRepository: DashboardRepository = {
  getOverview() {
    return apiClient.get<DashboardOverview>('/dashboard/overview');
  },
};
