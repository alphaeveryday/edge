---
name: edge-review
description: edge 저장소의 변경(diff·PR)을 Codex CLI 로 리뷰한다 — 정통 버그 + edge 고유 계약 위반(AGENTS 12룰·schema SSOT·신뢰경계·레이크 규약). "코드리뷰 해줘", "이 변경 봐줘", "PR 리뷰", "머지 전에 점검", diff 검토 요청 시 사용. effort(low·medium·high·max·ultra)로 깊이를, --comment/--fix 로 후처리를 고른다. 내장 /code-review 와 별개 — edge 규칙에 특화된 프로젝트판이다. 순수 문서 정합성은 docs-sync 소관.
---

# edge-review — edge 특화 코드 리뷰 (Codex 실행)

리뷰의 탐지·검증은 **Codex CLI (`codex exec`)** 가 수행한다. 이 스킬은 범위를 모아 지시문을 조립하고, Codex 가 못 하는 것(빌드·테스트 실행, PR 코멘트 게시, 수정 적용)을 맡는다.

규칙의 SSOT는 [AGENTS.md](../../../AGENTS.md)와 [docs/adr](../../../docs/adr) — 이 스킬과 그 문서가 충돌하면 문서를 따르고, 이 스킬의 갱신을 제안하라. 규칙 본문을 여기 복붙하지 않는다(Instruction File Convention). 규칙은 **번호로 인용**한다.

**범위 밖**: 코드-문서 드리프트 자체의 검출·동기화는 `docs-sync` 스킬 소관이다. 여기선 코드가 바꾼 '문서화된 사실'을 발견하면 finding 으로 포인터만 남기고 docs-sync 실행을 권한다.

## 전제 확인

`codex --version` 이 안 되면 즉시 중단하고 사용자에게 알려라 (`brew install codex`). 조용히 Claude 자체 리뷰로 폴백하지 마라 — 무엇이 리뷰했는지 사용자가 알아야 한다(Rule 12).

## effort 티어

인자로 받은 effort 가 지시문의 폭과 Codex 패스 수를 정한다. 미지정 기본 `medium`.

| effort | 지시문 각도 | 패스 | 출력 성향 |
|---|---|---|---|
| low·medium | 항상 각도 + 켜진 조건부 각도 | 1 | 적고 확실한 것 |
| high | 위 + 조건부 전부 | 1 | 넓게, 실질 위주 |
| max | 위 + 인접 미변경 코드까지 | 2 (독립 실행 후 dedup) | 넓게, 불확실도 일부 |
| ultra | 위 + 각 패스에 다른 중점 부여 | 3 (독립 실행 후 dedup) | 최대 커버리지 |

트리비얼 변경(오타·주석·설정 한 줄)엔 티어를 낮추거나 Codex 없이 즉답한다 — 과투자 금지(Rule 2).

## Phase 0 — 범위 수집 (이 스킬이 직접)

```bash
git diff dev...HEAD                       # 커밋된 브랜치 변경
git diff HEAD                              # 미커밋(staged+unstaged, 추적 파일)
git ls-files --others --exclude-standard   # 미추적(아직 add 안 된) 새 파일
git status --short
```
- **커밋된 브랜치 변경 + 미커밋(staged/unstaged) + 미추적 새 파일을 모두** 범위에 넣는다(합집합, fallback 아님). `git diff dev...HEAD` 는 커밋분만, `git diff HEAD` 는 추적 파일의 미커밋 변경만(**미추적 새 파일은 안 나온다**). 미추적 파일은 `git ls-files --others --exclude-standard` 로 나열해 **경로를 지시문에 명시**하고 Codex 에게 직접 읽으라고 지시한다 — line-by-line·시크릿 스캔이 새 config/소스에 든 버그·토큰을 놓치지 않게.
- 인자로 PR 번호·브랜치·경로가 오면 그 대상을 본다(`gh pr diff <N>`, 또는 커밋 SHA·베이스 브랜치를 지시문 범위로).
- **변경 영역을 판정**해 아래 조건부 각도를 켠다: schema(`libs/schema`)? · gateway/`*-api`(JVM 신뢰경계)? · `data-pipeline`/`analysis-engine`(Python 레이크)? · UI(`*-ui`/`ui-kit`)? · 전역 설정/CI? · **IaC**(`infra/`·terraform `.tf`/`.tftpl` — I각도)? · **검증·정규화·파싱·품질 게이트 코드**(`validate_*`·`check_*`·`normalize*`·타입 강제·게이트 — 언어 무관)?

## Phase 1 — Codex 실행

레포 루트에서, 티어가 정한 패스 수만큼 실행한다. 패스가 2 이상이면 **병렬로** 띄우고 출력 파일을 분리한다.

```bash
OUT=$(mktemp)                          # 패스마다 새 파일 — 재사용 금지
codex exec -s read-only \
  --output-schema .claude/skills/edge-review/findings.schema.json \
  -o "$OUT" \
  - <<'PROMPT'
<아래 지시문>
PROMPT
echo "codex exit=$?"                   # 0 이 아니면 이 패스는 실패다
```

- `-o` 파일에 스키마대로 된 JSON 이 떨어진다. stdout 은 진행 로그이니 파싱하지 마라.
- **출력 파일은 패스마다 `mktemp` 로 새로 만든다.** 고정 경로(`/tmp/edge-review-1.json`)를 재사용하면 codex 가 비정상 종료했을 때 이전 실행이 남긴 유효한 `{"findings":[]}` 가 그대로 읽혀, 실패한 리뷰가 "이슈 없음"으로 통과한다(Rule 12).
- **종료 코드를 반드시 확인한다.** codex 는 최종 메시지를 낸 경우에만 `-o` 파일을 쓰므로, 인증·네트워크·모델 오류로 죽으면 파일이 아예 없거나 낡은 것이다. exit≠0 이면 그 패스는 실패로 보고하고, 남은 패스 결과만으로 "리뷰 완료"라 하지 마라.
- **파싱 후 스키마 준수를 직접 검증한다.** `--output-schema` 는 모델에 스키마를 전달할 뿐 CLI 가 사후 검증을 보장하지 않는다. 각 finding 에 `file`·`line`·`severity`·`category`·`summary`·`failure_scenario`·`verdict` 가 다 있는지 확인하고, 빠진 게 있으면 그 finding 을 조용히 버리지 말고 결손을 명시해 보고하라.
- `-s read-only` 를 **명시한다.** 생략하면 샌드박스 정책이 사용자·프로젝트 설정에서 해석돼 `workspace-write` 가 될 수 있고, 그러면 리뷰 도중 모델이 작업트리를 고쳐 이후 빌드·`--fix` 범위가 오염된다. 리뷰는 읽기만 하면 된다. 빌드·테스트는 아래에서 이 스킬이 직접 돌린다.
- `codex exec review` 서브커맨드는 쓰지 않는다: edge 고유 계약을 모르고, 출력이 산문 요약이라 라인 앵커가 없다.

### 지시문 (프롬프트로 조립)

> 너는 edge 저장소의 코드 리뷰어다. 먼저 저장소 루트의 `AGENTS.md` 와 변경 파일 상위 폴더의 `AGENTS.md` 를 읽어라.
>
> **리뷰 대상**: {Phase 0 이 정한 범위 — diff 명령·커밋 SHA·미추적 파일 경로 목록}
>
> 각 각도로 후보를 내라. 후보는 `file`·`line`·한 줄 `summary`·구체적 `failure_scenario`를 가진다. 근거 있는 후보를 조용히 버리지 마라.

**항상 넣는 각도**
- **A. 정확성(line-by-line)** — 모든 헌크를 줄 단위로 + 변경 함수의 미변경 줄까지 읽는다. 뒤집힌 조건·off-by-one·None 역참조·falsy-zero·복붙 변수 오용·삼킨 예외·응답 형태 가정.
- **B. 제거된 동작** — 삭제/치환된 줄이 지키던 불변식을 이름 붙이고, 새 코드에서 그게 재확립됐는지 찾는다. 못 찾으면 후보(사라진 가드·좁아진 검증·지워진 테스트 케이스).
- **C. 교차 파일** — 변경 심볼의 호출부·피호출부를 grep 해 새 전제·바뀐 반환/예외·순서 의존이 콜사이트를 깨는지. **계약·와이어 형상 변경**(DTO·JSON 응답·`docs/contracts` 형상·스키마 경계면)이면 그 형상을 **역직렬화·파싱하는 소비자 코드**를 grep 해(예: `node.path("x")`·`.get("x")`·record 컴포넌트·`@JsonProperty`) 필드명·중첩·타입·nullable 이 실제로 일치하는지 대조한다 — DDL·스키마와만 맞추고 소비자 파싱과 어긋나면 런타임에 필드가 조용히 null 로 유실된다(ALPHA-395 실증: 계약은 nested `document.{title,source_uri}` 였는데 `publication-api` `ExplanationStore` 는 flat `{kind,title,source,published_at}` 를 파싱 — 형상 불일치를 로컬이 DDL 정확성만 봐 놓치고 봇이 잡음). **명세(openapi/JSON Schema) 작성·변경 시엔 필드명·타입에 더해 기계 어서션(`format`·`pattern`·`enum`·`minLength`·`required`·nullable)이 서버의 실제 accept/reject 집합과 일치하는지** 대조한다 — 명세가 서버보다 엄격하면 생성 클라이언트·게이트웨이가 서버는 받는 입력을 거부하고(반대면 통과), 산문(description)으로 완화를 적어도 기계 계약은 안 바뀐다. 서버 검증 코드(파싱·`isBlank`·`parse`·화이트리스트)를 읽어 명세 어서션이 그 accept 집합을 그대로 전사하는지 본다(ALPHA-498 실증: openapi `format: date` 가 빈 `trade_date=` 를 무효로 모델링했는데 서버 `parseTradeDate` 는 `isBlank` 로 생략 처리해 200 — 로컬은 형상만 보고 어서션-서버 불일치를 놓쳐 봇이 2라운드 왕복, `anyOf: [const "", format date]` 로 서버 수용집합 전사해 해소).
- **D. 거버넌스·규칙(AGENTS 12룰)** — **명백한 위반만**, 규칙을 번호·문구로 인용해 flag. 특히:
  - **Rule 12 fail-loud** — skip/실패를 조용히 삼키거나(로그 없이), 건너뛴 걸 success 로 위장하는가.
  - **Rule 3 surgical** — 이번 관심사 밖 코드를 "개선"하며 건드렸는가.
  - **Rule 9 tests-encode-WHY** — 테스트가 WHAT만 검사하고 WHY를 인코딩하지 못하는가. 비즈니스 로직이 바뀌어도 못 깨지는 테스트는 잘못됐다.
  - **Rule 2 simplicity / Rule 11 conformance** — 불필요한 추상화·투기적 코드 / 주변 컨벤션 이탈(단, 프로젝트가 Rule 3로 택한 의도적 복제는 감안).
- **시크릿 커밋 스캔(영역 무관)** — 변경된 **모든** 파일에서 커밋된 비밀값(api_key·토큰·private key·비밀번호). 코드뿐 아니라 `.github`·`infra`·config 샘플·UI 포함. 비밀값은 env 주입만 허용 — 커밋되면 최우선 finding.
- **재사용·단순화·효율(낮은 순위)** — 이미 있는 헬퍼 재구현(shared util 을 grep 해 이름을 대라)·파생 가능한 중복 상태·핫패스 낭비. edge 는 뉴스/가격처럼 Rule 3로 **의도적 복제**를 택한 지점이 있으니, 복제는 '동기화 안 되면 버그 나는' 경우(예: 상태 판정 로직)만 flag 하고 그 비용을 명시하라.

**조건부 각도 (Phase 0 이 켠 것만 지시문에 넣는다)**
- **E. 스키마 계약** (`libs/schema` 변경 **또는 DB 쓰기 코드**(리포지토리·`persist`·마트 적재 등) 변경 시) — 스키마 변경에 `generated` 모델 동반 갱신이 빠졌는가(README Git 원칙). 마이그레이션이 expand-contract(파괴적 DDL을 한 번에)를 어겼는가. **단일 writer 위반**([ADR-0005](../../../docs/adr/0005-db-as-contract.md)·[docs/implementation.md](../../../docs/implementation.md) §4) — 한 테이블을 소유 모듈 밖에서 INSERT/UPDATE/DELETE 하는가. 이 위반은 스키마 diff 가 아니라 **앱 DB 접근 코드**에서 터지므로, 스키마 변경이 없어도 DB 쓰기 코드가 바뀌면 검사한다.
- **F. 신뢰경계** (`gateway`·`*-api`·Sync 채널·Publication 코드 변경 시) — 하이브리드 경계([ADR-0010](../../../docs/adr/0010-hybrid-onprem-pivot.md)·[docs/context.md](../../../docs/context.md)) 위반: ① **Sync 채널** — Cloud→온프렘 Push 경로가 생겼는가(항상 온프렘 outbound Pull만). 테넌트 식별을 mTLS 인증서 바인딩 밖(파라미터·헤더)에서 받는가, 요청별 인증서-테넌트 인가 검증을 우회하는가([sync-auth.md](../../../docs/contracts/sync-auth.md)). ② **Publication API** (구 Serving API) — Published 외 상태(REVIEW_REQUIRED·BLOCKED·UNPUBLISHED 등)가 응답에 노출되는가. 원본 고객 ID/계좌를 받는 표면이 생겼는가(고객 식별 해시만 허용). ③ **데이터 거주지** — Cloud 저장 금지 데이터(고객 ID·노출 이력·최종 노출 문구 등, [data-residency.md](../../../docs/domain/data-residency.md))를 Cloud 쪽 코드가 저장하는가. ④ `gateway` 라우트 필터가 fail-open 인가(fail-closed 여야). cross-tenant 누수([ADR-0008](../../../docs/adr/0008-super-admin-console.md)).
- **G. 레이크·파이프라인** (`data-pipeline`·`analysis-engine` 변경 시) — 파티션 경로를 `lake/storage.py` 빌더(경로 규약 SSOT) 밖에서 조립했는가. raw 존이 원본을 유실하는가(raw 는 전부 보존). "결과는 항상 collection_log" 계약과 status 시맨틱(success/partial/error/stopped/skipped)이 온전한가 — 실패를 success(0건)로 위장하지 않는가(Rule 12).
- **H. 검증·품질 게이트 완전성** (검증·정규화·파싱·타입강제·품질 게이트 코드 변경 시. 이 코드가 diff 의 핵심이면 tier 무관하게 켠다 — 우회된 게이트는 Rule 12 blocker다) — 코드의 일이 '잘못된 데이터를 거르는 것'일 때, **malformed 입력이 실제로 드러나는지**(사유와 함께 실패) 아니면 (a) 게이트 전에 crash 하거나 (b) 통과값으로 강제되거나 (c) 게이트가 안 보는 필드로 우회해 **passed 로 인증**되는지 적대적으로 열거한다. '통과로 집계됨(records_passed 등)'이 hunt 대상이다. 아래 최소 입력군을 실제로 대입해 각 결과를 확인(테스트에 없으면 그 자체가 후보):
  - **crash-before-gate** — 비객체/비기대 타입 입력(`null`·`[]`·스칼라·키 누락)이 `.get()`·인덱싱·언패킹에서 터져 배치 전체를 죽이는가(행 단위로 격리돼야).
  - **coerce-to-passing** — 타입 강제가 bad 를 통과값으로 바꾸는가: `float("nan"/"inf")`(NaN 비교는 전부 False라 수치 게이트를 조용히 통과)·`int(-0.5)=0`·`float(True)=1.0`·관대한 `strptime`(`'202671'` 미패딩·`'20260231'` 비달력일)·공백만 문자열이 truthy.
  - **unchecked-field** — 게이트가 안 보는데 다운스트림 계약(정체성 키·참고값 등)이 요구하는 필드가 결측·불량이어도 passed 인가(예: `(market,ticker,trade_date)` 정체성 결측).
- **I. IaC 교체·상태 안전성** (terraform·`infra/` 변경 시) — 인프라 변경이 **의도 밖의 리소스 교체·상태 손실**을 부르는지. 코드가 보통 넘어가는 이 함정들을 적대적으로 확인한다:
  - **ForceNew 교체 함정** — 값이 바뀌면 리소스를 **destroy+recreate** 하는 replacement-only 속성(`aws_instance.ami`·`subnet_id`·`availability_zone`, `aws_db_instance` 일부 등)이 이 변경으로 드리프트하는가. 특히 **data-source 재조회**(`data.aws_ami` 필터·`most_recent`)나 var 기본값 변경으로 값이 달라지면, 무관한 apply(IAM·SG 한 줄)마저 stateful 리소스(루트 EBS·`aws_ebs_volume`·PG/Redis named volume·RDS)를 통째로 갈아엎어 **데이터가 유실**된다. `lifecycle { ignore_changes = [...] }`·`prevent_destroy`·명시적 `-replace` 경로 같은 방어가 있는지 확인 — 없으면 후보(교체가 plan 에 드러나는지 `terraform plan` 으로 검증 권함). "신규 리소스만 반영"이라는 주석·PR 설명은 **코드로 강제되지 않으면 근거가 아니다**.
  - **와일드카드·most_recent 오선택** — AMI·이미지·리소스 이름 글롭이 의도 밖 변종을 고르는가(예: `al2023-ami-*` 가 ECS-optimized·minimal 변종을, `*-latest` 가 프리릴리스를 매치). `most_recent` 는 명명 규칙이 바뀌면 조용히 다른 이미지를 집으므로, 필터가 원하는 계열만 앵커하는지 본다.
  - **시크릿·state 노출** — SecureString·비밀값을 terraform 이 관리해 평문이 state 에 들어가는가(demo-onprem 패턴 — cert 는 ARN 만 구성하고 값은 운영자 CLI 주입). `ignore_changes` 로 가려선 안 되는 보안 관련 드리프트를 가리는가.

**검증 (같은 실행 안에서)**
> 각 후보를 적대적으로 검증해 **CONFIRMED**(코드로 확증 — 실제 줄을 인용) 또는 **PLAUSIBLE**(현실적이나 미확증) 만 남겨라. 코드로 반증 가능한 것(실제 줄·불변식·이 diff 의 가드를 인용)은 버려라. 현실적 상태(경쟁·드문 에러 경로의 None·falsy-zero·경계 off-by-one·부분 실패)는 기본 PLAUSIBLE. 규칙 위반은 **정확한 규칙 번호와 정확한 줄을 인용할 수 있을 때만** 낸다 — 취향·막연한 "정신" 추론은 금지. 결함이 없으면 빈 배열을 반환하라. 지정된 JSON 스키마로만 응답하라.

## Phase 2 — 취합

- 패스가 여럿이면 **dedup**: 같은 결함·같은 위치·같은 사유 → 하나만. verdict 가 갈리면 높은 쪽(CONFIRMED)을 취한다.
- **한 패스만 낸 finding 은 다수결로 죽이지 마라** — 다른 패스가 못 본 것일 뿐 반증이 아니다(미스의 주범). 대신 `max`·`ultra` 에서는 그런 finding 만 모아 **검증 패스를 한 번 더** 돌린다. 파인더와 검증이 같은 실행 안에 있어 한 패스의 오독이 아무 반증도 못 받는 구조라, 패스를 늘리는 것만으로는 precision 이 오르지 않기 때문이다:
  ```
  아래 finding 들은 한 번의 리뷰에서만 나왔다. 각각을 반증하라 —
  실제 코드 줄·불변식·기존 가드를 인용해 반박할 수 있으면 REFUTED,
  확증되면 CONFIRMED, 반증도 확증도 안 되면 PLAUSIBLE.
  ```
  REFUTED 만 버리고 나머지는 살린다. `verdict` enum 이 `REFUTED` 를 포함하는 건 **이 검증 패스 전용**이다 — 파인더 패스는 위 지시대로 반증된 후보를 애초에 내지 않으므로 REFUTED 를 쓸 일이 없고, 파인더가 REFUTED 를 냈다면 지시문을 어긴 것이니 그대로 버리지 말고 보고하라. `low`~`high` 는 패스가 하나뿐이라 이 단계가 없다 — 그 티어의 결과는 단독 검증분임을 출력에 밝힌다.
- Codex 가 낸 `file`·`line` 이 실제 변경 범위에 있는지 확인하고, 어긋나면 앵커를 바로잡거나 낮은 신뢰로 강등하라.

## 빌드/테스트 확인 (이 스킬이 직접 — Codex 는 못 한다)

Codex 는 read-only 샌드박스라 테스트를 돌리지 못한다. 변경 모듈의 빌드·테스트가 통과하는지 **여기서** 확인하고, 실패는 **최우선 finding**으로 올린다(Rule 12 — "통과"라 말하려면 실제로 통과해야 함). 세 런타임의 워크스페이스 루트가 모두 `src/`(settings.gradle·pnpm-workspace.yaml·pyproject.toml)이므로 **명령은 `src/` 에서** 돌린다.
- JVM: `./gradlew :<apps|libs>:<모듈>:build` (앱은 `:apps:{cloud|onprem}:*`, 공유 라이브러리는 `:libs:*`. 예: `:apps:onprem:tenant-console-api`·`:libs:schema`·`:libs:jvm-common`)
- Node: 패키지 `package.json` 의 scripts 를 먼저 보고 **정의된 것만** 돌린다 — `pnpm --filter <패키지> build`·`typecheck`·`test` 중 존재하는 것. `test` 스크립트가 없는 패키지(예: tenant-console-ui)에 `test` 만 돌리면 아무 검증 없이 exit 0 이라 통과로 오인한다.
- Python: `uv run --package <패키지> pytest` — uv 워크스페이스가 `src/pyproject.toml` 이라 반드시 `src/` 에서(레포 루트에선 pyproject 를 못 찾아 실패). uv 없으면 모듈 `.venv/bin/pytest`.

## 출력

finding 을 **가장 심각한 순**으로 낸다. `ReportFindings` 툴이 있으면 그걸로 보고하고 텍스트로 중복 출력하지 않는다(없으면 랭크된 목록). 정확성 버그가 cleanup·규칙 finding 보다 항상 우선하고, 티어 상한을 넘으면 상위만 남긴다. 아무것도 안 남으면 "정합 — 실질 이슈 없음"을 명시한다(점검 자체가 산출물). **무엇이 리뷰했는지 한 줄로 밝힌다** — Codex 를 돌렸으면 패스 수와 실패한 패스가 있었는지, 트리비얼 변경이라 Codex 없이 즉답했으면 그 사실을. 돌리지 않은 리뷰를 돌린 것처럼 쓰지 마라(Rule 12).

**후처리 플래그**
- `--comment` — finding 을 PR **라인 앵커드 인라인 코멘트**로 게시. `gh pr comment`·`gh pr review` 는 path/line 옵션이 없어 본문 코멘트만 되므로 쓰지 않고, review comments API 를 쓴다: `gh api repos/{owner}/{repo}/pulls/$PR/comments -f path=… -F line=… -f side=RIGHT -f commit_id=… -f body=…` (`gh api` 는 `{owner}`·`{repo}`·`{branch}` 만 치환하므로 **PR 번호는 셸 변수 `$PR` 로 직접** 넣는다). PR 대상일 때만.
- `--fix` — 검증된 finding 을 작업트리에 적용(정확성 수정 우선, cleanup 은 명확한 것만). **이 스킬이 직접 편집한다** — Codex 샌드박스는 쓰기 권한이 없다. 적용 후 테스트를 재실행하고, 편집분을 다시 리뷰 범위에 넣는다.

## 경계·주의

- 내장 `/code-review` 와 **이름이 다르다**(`edge-review`) — 혼동 방지. 내장은 범용, 이건 edge 규칙 특화판이다.
- 문서 드리프트 정정은 하지 않는다 — `docs-sync` 로 넘긴다.
- Codex 가 낸 지적도 무비판 수용은 금지 — 결함인지 의도인지 분류해 처리한다.
