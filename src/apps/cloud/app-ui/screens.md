# ETF Orca 화면 명세 (1단계)

출처: Claude Design 프로젝트 "스토리 뷰어 전망 변화 표시" → `MarketBrew App v188 copy 2.dc.html` (사본 `design/app-v188.dc.html`).
템플릿(마크업)은 완전하고, 스크립트(목 데이터·핸들러)는 256KiB 제한으로 `EXTRA_ETFS.NUKE` 중간에서 잘렸다. 잘린 뒤쪽 = 나머지 목 데이터 + 이벤트 핸들러.
pptx 32장(`tmp.md`)보다 이 파일이 최신이며 화면이 더 많다. 제품명은 ETF Orca (디자인 원문의 MarketBrew·EDGE 는 치환 대상).

## 화면 (풀스크린 26)

| 디자인 플래그 | 화면 | pptx 대응 | 비고 |
|---|---|---|---|
| obIsHow | 온보딩 1 · 뉴스 3만 건 | 온보딩 1 | |
| obIsSticker | 온보딩 2 · 전망 스티커 5단계 | 온보딩 2 | |
| obIsWhat | 온보딩 3 · 오늘 달라진 것 | 온보딩 3 | 카드 스택 애니메이션 |
| obIsTheme | 테마 선택 | 테마 선택 | 그룹별 칩(obThemeGroups) |
| obIsEtf | ETF 선택 | ETF 선택 | 검색·빈 상태 포함 |
| isSignup | 로그인 | 로그인 | Apple·Google·이메일 |
| isHome | 홈 | 홈 | 관심 그룹 탭(wGroups)·오늘 등락·빈 상태(watchEmpty) |
| isDiscoverDark | 관심 | 관심 | 빈 상태 포함 |
| isWatchEdit | 관심 편집 | 관심 편집 | 드래그 정렬·종목 추가 |
| isSearching | 검색 | 검색 | 최근 검색 칩·결과 없음 |
| isStock · isStockSummary | ETF 상세 · 요약(오늘 움직임) | ETF 상세·요약 | 차트 range 1D~MAX, "왜 움직였을까" |
| isStock · isStockBrief | ETF 상세 · AI 분석 | ETF 상세·AI 분석 | 단기 전망 스티커·요인 5행 |
| isStock · isStockData | ETF 상세 · 종목정보 | (없음, tmp.md 누락 목록) | 구성 히트맵·테마 비중·보유 종목 |
| isStock · isStockComm | ETF 상세 · 커뮤니티 | ETF 상세·커뮤니티 | 투표(pollSt)·글 목록 |
| isIssueList | 이슈 목록 | 이슈 | 관심 기준 빈 상태 |
| isLiveIssue | 실시간 이슈 상세 | 실시간 이슈 | 주목 포인트·출처(liSrcOpen)·연관 ETF |
| isCommunity | 커뮤니티 | 커뮤니티 | 투표(pollComm)·글쓰기 진입(commCanWrite) |
| isExplore | 탐색 | 탐색 | 대기(exIdle)·검색(exSearching)·테마 시트 |
| isExCmp | 탐색 · 테마 ETF 비교 | 테마 ETF | "전망 좋은 순", 데일리 준비 중 상태 |
| isThemes | 테마 목록 | 테마 | |
| isThemeDetail | 테마 분석 | 테마 분석 | 오늘 반영·무엇이 중요한가·우리의 전망·종목 시트 |
| isCommProfile | 커뮤니티 프로필 | 커뮤니티 프로필 | 빈 상태(cpEmpty) |
| isProfile | 계정 | 계정 | 알림 토글·로그아웃 |
| isNotis | 알림 | 알림 | 종류 필터·빈 상태 |
| storyOpen | 스토리 뷰어 | 스토리 | 진행 바·일시정지·전망 변화(stoChanged)·관련 이슈·차트 |
| postOpen | 게시물 상세 | 게시물 | 답글·인용 리포스트 |

## 시트·모달·오버레이 (19)

| 플래그 | 내용 | 뜨는 곳 |
|---|---|---|
| menuOpen | 전체 메뉴 (로그인/비로그인 상태) | 상단바 |
| factorPageOpen | 요인 상세 (단기 판단 기준) | AI 분석 |
| metricOpen | 요인 지표 (도움·중립·부담) | AI 분석 |
| daSheetOpen | 데일리 분석 시트 (5기준·출처) | AI 분석 |
| briefOpen | 한 줄 요약 → EDGE 판단 보기 | 요약 |
| sumEvtOpen | 요약 사건 상세 | 요약 |
| hintOpen | 힌트/설명 | 요약 |
| stockSheetOpen | 테마 내 종목 시트 | 테마 분석 |
| themeSheetOpen | 테마 ETF 시트 | 탐색 |
| pickOpen | 그룹 담기 | ETF 상세·검색 |
| newGroupOpen · groupEditOpen · delGroupOpen | 그룹 생성·편집·삭제 확인 | 관심 편집·그룹 담기 |
| moveOpen | 그룹 이동 | 관심 편집 |
| commWriteOpen | 글쓰기 | 커뮤니티·ETF 커뮤니티 |
| postMoreOpen | 게시물 더보기 (신고·삭제) | 게시물 |
| liSrcOpen | 출처 목록 | 실시간 이슈 |
| sumToastOn | 토스트 | 전역 |
| showTopBar · showTabs | 상단바·탭바 (관심·탐색·커뮤니티 3탭, 홈은 상단바) | 전역 |

제외 확정(2026-09-28): 투자 에이전트 채팅(`agentContext`, 미니플레이어), 인사이트 상세(`isInsight`, 홈 `feedMode` 노브는 미사용 잔재). 홈은 ETF 기반 레이아웃 하나로 간다.

## Expo Router 트리 (안)

```
app/
  _layout.tsx                # 루트 스택 + 온보딩 게이트
  onboarding/
    _layout.tsx              # 가로 스와이프 스택
    [step].tsx               # how · sticker · what · theme · etf
  login.tsx
  (tabs)/
    _layout.tsx              # 탭바: home · watch · explore · community
    home.tsx
    watch/index.tsx
    watch/edit.tsx
    explore/index.tsx
    explore/compare.tsx      # isExCmp
    explore/themes.tsx
    explore/themes/[id].tsx  # isThemeDetail
    community/index.tsx
  etf/[code]/_layout.tsx     # 상단 헤더 + 4탭 세그먼트
  etf/[code]/summary.tsx
  etf/[code]/brief.tsx
  etf/[code]/data.tsx
  etf/[code]/community.tsx
  issues/index.tsx
  issues/[id].tsx            # isLiveIssue
  search.tsx
  story/[etf].tsx            # 모달 프레젠테이션
  post/[id].tsx
  profile/index.tsx          # 계정
  profile/community.tsx      # 커뮤니티 프로필
  notifications.tsx
```

시트·모달은 라우트가 아니라 컴포넌트(bottom-sheet)로 두고, 필요한 것만 `presentation: 'modal'` 라우트로 승격한다(스토리·글쓰기·게시물 더보기).

## 상태·데이터 (디자인 state 기준)

- 사용자: 로그인 여부, 닉네임·핸들·아바타, 알림 설정, 테마
- 온보딩: step, 고른 테마, 고른 ETF
- 관심: `watch[]`, 그룹(wGroups), 정렬
- ETF: `ETFS`(AXAI·GRID·KBND·DEFN·MEDX·SOLR) + `EXTRA_ETFS`(SEMI·NUKE·…, 잘림) — 이름·가격·등락·스티커·요인 5행·히트맵·테마·보유종목·데일리 본문·일정
- 이슈: `ISSUES[]` (제목·메타·포인트·본문·연결 ETF)
- 커뮤니티: 게시물·답글·좋아요·인용 리포스트·투표(`pollVotes`)
- 알림: 종류별 목록·배지
- 차트: `CFG` range 별 계수(1D·1W·1M·6M·1Y·5Y·MAX)

## 미결

1. 잘린 스크립트 뒤쪽(목 데이터 나머지·핸들러) 확보 방법 — Claude Design 에서 파일 내보내기.
