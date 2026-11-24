# 기능 지도 — ETF Orca 앱

사용자가 만지는 기능 하나에 파일 하나. 각 파일은 사용자 시점으로 쓴다: 무엇인지, 어떻게 가는지, Maestro 로 어떻게 조작하는지, 무엇이 보이면 동작한다고 보는지.
화면 목록의 정본은 `apps/etforca/app-ui/screens.md`(화면 24개)다. 아래 표에 없는 화면은 아직 자동 검증이 없다.

| 기능 | 파일 | 흐름 | 상태 |
|---|---|---|---|
| 온보딩 | [onboarding.md](onboarding.md) | `flows/onboarding.yaml` (onboarding 태그, 기본 실행 제외) | 단계별로 조작 확인(2026-11-25). 흐름 파일 통째 실행은 게스트가 생겨 아직 안 함 |
| 홈 | [home.md](home.md) | `flows/home.yaml` | 통과 2026-11-25 |
| ETF 상세 4탭 | [etf-detail.md](etf-detail.md) | `flows/etf-detail.yaml` | 통과 2026-11-25 |
| mock 화면 둘러보기 (디자인 기준용) | app-design SKILL.md | `flows/mock/tour.yaml` (mock 빌드 전용) | 통과 2026-11-25, 두 번 실행 차이 0% |

다음 후보(screens.md 순): 관심·관심 편집, 검색, 탐색, 커뮤니티 목록·게시물, 알림, 전체 메뉴. 로그인·회원가입·글쓰기·투표는 쓰기라 자동 검증에서 뺀다(사람 확인).
