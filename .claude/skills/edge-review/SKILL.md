---
name: edge-review
description: edge 저장소의 변경(diff·PR)을 정통 버그 + edge 고유 계약 위반(AGENTS 12룰·schema SSOT·신뢰경계·레이크 규약)까지 함께 리뷰한다. "코드리뷰 해줘", "이 변경 봐줘", "PR 리뷰", "머지 전에 점검", diff 검토 요청 시 사용. effort(low·medium·high·max·ultra)로 깊이를, --comment/--fix 로 후처리를 고른다. 내장 /code-review 와 별개 — edge 규칙에 특화된 프로젝트판이다. 순수 문서 정합성은 docs-sync 소관.
---

# edge-review — edge 특화 코드 리뷰

변경(diff/PR)을 두 축으로 본다: **정통 버그**(정확성·회귀·교차파일)와 **edge 고유 계약 위반**(거버넌스·스키마·신뢰경계·레이크). 규칙의 SSOT는 [AGENTS.md](../../../AGENTS.md)와 [docs/adr](../../../docs/adr) — 이 스킬과 그 문서가 충돌하면 문서를 따르고, 이 스킬의 갱신을 제안하라. 규칙 본문을 여기 복붙하지 않는다(Instruction File Convention). 규칙은 **번호로 인용**한다.

**범위 밖**: 코드-문서 드리프트 자체의 검출·동기화는 `docs-sync` 스킬 소관이다. 여기선 코드가 바꾼 '문서화된 사실'을 발견하면 finding 으로 포인터만 남기고 docs-sync 실행을 권한다.

## effort 티어

인자로 받은 effort 가 폭(recall)과 검증(precision)을 정한다. 미지정 기본 `medium`.

| effort | 파인더 폭 | 검증 | 출력 성향 |
|---|---|---|---|
| low·medium | 핵심 각도만, 조건부 각도 최소 | 후보별 1표, 고신뢰만 통과 | 적고 확실한 것 |
| high | 전 각도 + 해당 조건부 각도 | 후보별 1표, recall 편향 | 넓게, 실질 위주 |
| max | 전 각도, 인접 미변경 코드까지 | 후보별 다표결 | 넓게, 불확실도 일부 포함 |
| ultra | 전 각도 + 조건부 전부, 다회전 | 다표결 adversarial + 종합 | 최대 커버리지 |

트리비얼 변경(오타·주석·설정 한 줄)엔 티어를 낮추거나 즉답한다 — 과투자 금지(Rule 2).

## Phase 0 — 범위 수집

```bash
git diff dev...HEAD   # 커밋된 브랜치 변경
git diff HEAD         # 미커밋(staged+unstaged) 변경
git status --short
```
- **커밋된 브랜치 변경과 미커밋 작업트리 변경을 둘 다** 범위에 넣는다(합집합, fallback 아님). 브랜치에 이미 커밋이 있어도(예: `--fix` 후·최종 커밋 전) 미커밋 편집이 line-by-line·시크릿 스캔을 빠져나가면 안 된다. `git diff dev...HEAD` 는 커밋분만, `git status --short` 는 파일명만 주므로 `git diff HEAD` 로 미커밋 내용을 실제로 읽는다.
- 인자로 PR 번호·브랜치·경로가 오면 그 대상을 본다(`gh pr diff <N>`).
- **변경 영역을 판정**해 아래 조건부 각도를 켠다: schema(`libs/schema`)? · gateway/`*-api`(JVM 신뢰경계)? · `data-pipeline`/`analysis-engine`(Python 레이크)? · UI(`*-ui`/`ui-kit`)? · 전역 설정/CI/infra?

## Phase 1 — 파인더 각도 (Agent 로 독립 병렬 실행)

각 각도는 `file`·`line`·한 줄 `summary`·구체 `failure_scenario`를 가진 후보를 낸다. 근거 있는 후보를 조용히 버리지 마라 — 버려진 후보는 검증을 못 거쳐 미스의 주범이다.

**항상 도는 각도**
- **A. 정확성(line-by-line)** — 모든 헌크를 줄 단위로 + 변경 함수의 미변경 줄까지 읽는다. 뒤집힌 조건·off-by-one·None 역참조·falsy-zero·복붙 변수 오용·삼킨 예외·응답 형태 가정.
- **B. 제거된 동작** — 삭제/치환된 줄이 지키던 불변식을 이름 붙이고, 새 코드에서 그게 재확립됐는지 찾는다. 못 찾으면 후보(사라진 가드·좁아진 검증·지워진 테스트 케이스).
- **C. 교차 파일** — 변경 심볼의 호출부·피호출부를 Grep 해 새 전제·바뀐 반환/예외·순서 의존이 콜사이트를 깨는지.
- **D. 거버넌스·규칙(AGENTS 12룰)** — 해당 [CLAUDE.md](../../../CLAUDE.md)·[AGENTS.md](../../../AGENTS.md)(+변경 파일 상위 폴더의 것)를 읽고 **명백한 위반만**, 규칙을 번호·문구로 인용해 flag. 특히:
  - **Rule 12 fail-loud** — skip/실패를 조용히 삼키거나(로그 없이), 건너뛴 걸 success 로 위장하는가.
  - **Rule 3 surgical** — 이번 관심사 밖 코드를 "개선"하며 건드렸는가.
  - **Rule 9 tests-encode-WHY** — 테스트가 WHAT만 검사하고 WHY(왜 그 동작이 중요한지)를 인코딩하지 못하는가. 테스트가 비즈니스 로직이 바뀌어도 못 깨지면 잘못됐다.
  - **Rule 2 simplicity / Rule 11 conformance** — 불필요한 추상화·투기적 코드 / 주변 컨벤션 이탈(단, 프로젝트가 Rule 3로 택한 의도적 복제는 감안).

- **시크릿 커밋 스캔(항상·영역 무관)** — 변경된 **모든** 파일에서 커밋된 비밀값(api_key·토큰·private key·비밀번호)이 박혔는지. 코드뿐 아니라 `.github`·`infra`·config 샘플·UI 포함(Phase 0 이 전역 config/CI/infra 를 영역으로 분류하므로 거기 새는 키를 놓치면 안 된다). 비밀값은 env 주입만 허용 — 커밋되면 최우선 finding.

**cleanup 각도(항상, 낮은 순위)**
- **재사용·단순화·효율** — 이미 있는 헬퍼 재구현(shared util Grep 해 이름 대라)·파생 가능한 중복 상태·핫패스 낭비. edge 는 뉴스/가격처럼 Rule 3로 **의도적 복제**를 택한 지점이 있으니, 복제는 '동기화 안 되면 버그 나는' 경우(예: 상태 판정 로직)만 flag 하고 그 비용을 명시하라.

**조건부 각도 (변경 영역이 켜질 때만)**
- **E. 스키마 계약** (`libs/schema` 변경 **또는 DB 쓰기 코드**(리포지토리·`persist`·마트 적재 등) 변경 시) — 스키마 변경에 `generated` 모델 동반 갱신이 빠졌는가(README Git 원칙). 마이그레이션이 expand-contract(파괴적 DDL을 한 번에)를 어겼는가. **단일 writer 위반**([ADR-0005](../../../docs/adr/0005-db-as-contract.md)·[docs/schema.md](../../../docs/schema.md) §1) — 한 테이블을 소유 모듈 밖에서 INSERT/UPDATE/DELETE 하는가. 이 위반은 스키마 diff 가 아니라 **앱 DB 접근 코드**에서 터지므로, 스키마 변경이 없어도 DB 쓰기 코드가 바뀌면 검사한다.
- **F. 신뢰경계** (`gateway`·`*-api` 변경 시) — `widget-api`에 쓰기 표면이 생겼는가(읽기 전용·좁은 표면이어야 함). `gateway` 라우트 필터가 fail-open 인가(fail-closed 여야, [ADR-0006](../../../docs/adr/0006-gateway-single-edge.md)). cross-tenant 누수([ADR-0008](../../../docs/adr/0008-super-admin-console.md)). (시크릿 커밋은 위 '항상' 각도가 영역 무관하게 스캔한다.)
- **G. 레이크·파이프라인** (`data-pipeline`·`analysis-engine` 변경 시) — 파티션 경로를 `lake/storage.py` 빌더(경로 규약 SSOT) 밖에서 조립했는가. raw 존이 원본을 유실하는가(raw 는 전부 보존). "결과는 항상 collection_log" 계약과 status 시맨틱(success/partial/error/stopped/skipped)이 온전한가 — 실패를 success(0건)로 위장하지 않는가(Rule 12).

## Phase 2 — 검증

근접 중복 후보를 dedup(같은 결함·같은 위치·같은 사유 → 하나만) 한 뒤, 남은 후보마다 verifier 를 돌려 **CONFIRMED / PLAUSIBLE / REFUTED** 중 하나를 받는다(티어별 표수). 현실적 상태(경쟁·드문 에러 경로의 None·falsy-zero·경계 off-by-one·부분 실패)는 기본 PLAUSIBLE. REFUTED 는 코드로 반증 가능할 때만(실제 줄 인용·불변식 제시·이 diff 의 가드 인용). CONFIRMED·PLAUSIBLE 만 남긴다.

## 빌드/테스트 확인

변경 모듈의 빌드·테스트가 통과하는지 확인하고, 실패는 **최우선 finding**으로 올린다(Rule 12 — "통과"라 말하려면 실제로 통과해야 함). 런타임별:
- JVM: `./gradlew :apps:<모듈>:build` (src/ 에서)
- Node: `pnpm --filter <패키지> test`
- Python: `uv run --package <패키지> pytest` (또는 모듈 `.venv/bin/pytest`)

## 출력

검증 통과 finding 을 **가장 심각한 순**으로 낸다. `ReportFindings` 툴이 있으면 그걸로 보고하고 텍스트로 중복 출력하지 않는다(없으면 랭크된 목록). 정확성 버그가 cleanup·규칙 finding 보다 항상 우선하고, 티어 상한을 넘으면 상위만 남긴다. 아무것도 안 남으면 "정합 — 실질 이슈 없음"을 명시한다(점검 자체가 산출물).

**후처리 플래그**
- `--comment` — finding 을 PR **라인 앵커드 인라인 코멘트**로 게시. `gh pr comment`·`gh pr review` 는 path/line 옵션이 없어 본문 코멘트만 되므로 쓰지 않고, review comments API 를 쓴다: `gh api repos/{owner}/{repo}/pulls/{N}/comments -f path=… -F line=… -f side=RIGHT -f commit_id=… -f body=…`. PR 대상일 때만.
- `--fix` — 검증된 finding 을 작업트리에 적용(정확성 수정 우선, cleanup 은 명확한 것만). 적용 후 테스트 재실행.

## 경계·주의

- 내장 `/code-review` 와 **이름이 다르다**(`edge-review`) — 혼동 방지. 내장은 범용, 이건 edge 규칙 특화판이다.
- 문서 드리프트 정정은 하지 않는다 — `docs-sync` 로 넘긴다.
- 규칙 위반은 **인용 가능할 때만** flag(정확한 규칙 번호·문구 + 정확한 줄). 취향·막연한 "정신" 추론은 금지.
