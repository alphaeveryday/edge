# ETF Orca 화면 명세 (v3)

정본: Claude Design 프로젝트 "스토리 뷰어 전망 변화 표시" 의 `MarketBrew App v188 copy 2 copy v3.dc.html` (사본 `design/app-v188-v3.dc.html`, 완전본). 제품명은 ETF Orca. 디자인 원문의 MarketBrew·EDGE 는 치환 대상.

## 화면 28개

| 디자인 화면 | 라우트 | 비고 |
|---|---|---|
| 온보딩 1~3 | `onboarding/{how,sticker,what}` | 뉴스 3만 건 · 전망 스티커 5단계 · 오늘 달라진 것 |
| 테마 선택 · ETF 선택 | `onboarding/{theme,etf}` | ETF 선택 완료 → 홈. 가입 화면은 첫 진입에 없다 |
| 홈 | `(tabs)/home` | 그룹 칩 · 전망 강도 카드 · 관심 top3(+더보기) · 커뮤니티 인기글 |
| 관심 · 관심 편집 | `(tabs)/watch` · `watch/edit` | 그룹 칩 · 종목 추가(검색) |
| 검색 | `search` | 최근 검색 · 결과 없음 |
| ETF 상세 4탭 | `etf/[code]/{brief,summary,community,data}` | AI 분석 · 오늘 움직임 · 커뮤니티 · 종목정보 |
| 요인 상세 · 요인 지표 | `factor/[code]/[axis]` · `metric/[code]/[axis]` | AI 분석 탭에서 진입 |
| 탐색 | `(tabs)/explore` | AI 순위 목록 하나. 행 클릭 → 분석 상세 시트 |
| 테마 · 테마 분석 | `themes` · `themes/[id]` | 정렬 칩 "전체" 하나 |
| 이슈 · 실시간 이슈 | `issues` · `issues/[id]` | 연관 ETF 행에 스티커·가격·관심 토글 |
| 커뮤니티 · 글쓰기 · 게시물 | `(tabs)/community` · `community/write` · `post/[id]` | 글쓰기 FAB 는 분석 있는 관심 ETF 가 있을 때 |
| 커뮤니티 프로필 · 계정 | `profile/community` · `profile` | 알림 토글 · 로그아웃 |
| 알림 | `notifications` | 전체/관심/커뮤니티 |
| 전체 메뉴 | `menu` | 로그인 진입 "로그인하고 시작하기" |
| 로그인 · 회원가입 · 비밀번호 재설정 | `login` · `auth/signup` · `auth/reset` | 모달, 우상단 닫기. 로그인 = 워드마크 + 이메일·비밀번호 + 회원가입·비밀번호 찾기. 소셜 로그인은 SDK 도입 전까지 없음(v3 의 CTA 3개 화면에서 변경, 이메일 1차 출시) |

시트(컴포넌트): 분석 상세(`DailySheet`, 투표 카드 상단·다음 ETF 순환) · 오늘 움직임 상세(`MoveSheet`) · 힌트 · 그룹 담기 · 그룹 생성/편집/삭제 · 로그인 유도 · 면책 고지 · 출처 목록 · 글 삭제.

사라진 화면(v3): 스토리 뷰어, 인사이트, 테마 ETF 시트(탐색), 테마 ETF 비교, 투자 에이전트 채팅.

## 흐름

- 온보딩 5단계 → 홈. 로그인은 전체 메뉴 또는 로그인이 필요한 동작(투표·글쓰기·답글·좋아요)의 유도 시트에서. 로그인·가입 성공과 닫기는 원래 화면으로 복귀(가입은 로그인 화면에서 진입). 로그아웃·탈퇴 → 온보딩.
- 탭 4개: 홈 · 관심 · 탐색 · 커뮤니티. 이슈·테마·검색·알림·관심 편집은 전체 메뉴에서.
- 홈 관심 줄 → ETF 상세 오늘 움직임 탭. 발행본이 없으면 토스트 "아직 AI 분석이 준비되지 않았어요".
- 관심 줄 → ETF 상세 AI 분석 탭.
- 탐색 행 → 분석 상세 시트(투표 카드 · 오늘 추가된 것 · 논거 · 그래서 · 전망 변화 · 5기준 · 출처 · 다음 ETF). 분석이 없으면 토스트. 시트에서 ETF 상세 AI 분석 탭으로.
- ETF 상세 AI 분석 탭: 날짜 스트립 · 단기 전망 · 이전→지금 · 종합 · 축 칩(→ 요인 지표) · 분석 자세히 보기(→ 분석 상세 시트).
- 게스트 범위: 조회·관심·온보딩은 디바이스 단위로 허용. 투표·글·답글·좋아요는 회원. 게스트 관심 상한 없음.

## Expo Router 트리

```
app/
  _layout.tsx                # 루트 스택 + 폰트 + Toast + 로그인 유도 시트
  index.tsx                  # 온보딩 게이트
  onboarding/{how,sticker,what,theme,etf}.tsx
  login.tsx (모달)           # 이메일 로그인
  auth/signup.tsx (모달)     # 이메일 가입
  auth/reset.tsx (모달)
  (tabs)/{home,explore,community}.tsx  watch/index.tsx
  watch/edit.tsx
  etf/[code]/_layout.tsx     # 헤더 + 4탭
  etf/[code]/{brief,summary,community,data}.tsx
  factor/[code]/[axis].tsx  metric/[code]/[axis].tsx
  themes/index.tsx  themes/[id].tsx
  issues/index.tsx  issues/[id].tsx
  search.tsx  menu.tsx  notifications.tsx
  post/[id].tsx  community/write.tsx (모달)
  profile/index.tsx  profile/community.tsx
```

## 데이터 경계

- 서버(`src/api/http`): 계약 `app-api/openapi.yaml`. 어댑터가 계약 응답을 앱 타입으로 바꾼다.
- 앱에서 끝내는 것: 최근 본 ETF(메모리), 용어 힌트(번들), 내 투표 선택(메모리), 다음 ETF 순환(순위 목록으로 계산).
- 상태 화면: `src/components/state/` Loading · Pending · ErrorView · QueryState. `ApiError.code` NOT_READY → 준비 중, 그 외 → 오류.

## 잔여

인용 리포스트 글쓰기, 타인 프로필, 신고 사유 선택, 알림 설정 상세, 그룹 이름 변경, 약관·처리방침 본문. 보류: 드래그 정렬, 날짜 주 이동, 탐색 내 검색, 온보딩 애니메이션, 테마 사진.
