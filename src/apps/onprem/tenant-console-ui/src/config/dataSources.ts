/**
 * 도메인별 데이터 소스 스위치.
 *
 * 백엔드 API가 도메인별로 완성될 때마다 "해당 도메인의 한 줄만" 'mock' → 'real' 로
 * 바꾸면 그 도메인 전체가 실연동으로 교체된다. 페이지/컴포넌트는 이 파일을 직접 보지
 * 않는다 — 각 도메인의 index.ts 가 이 값을 읽어 mock|real repository 중 하나를 export 한다.
 *
 * @see domains/<domain>/index.ts
 */
export type DataSourceMode = 'mock' | 'real';

export type Domain = 'explanations' | 'screening' | 'scope' | 'users' | 'session';

export const DATA_SOURCES: Record<Domain, DataSourceMode> = {
  explanations: 'mock',
  screening: 'mock',
  scope: 'mock',
  users: 'mock',
  session: 'mock',
};
