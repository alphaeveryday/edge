# Investment Lab

305720(KODEX 2차전지산업)와 원화 현금만 운용하는 로컬 PIT 투자 연구 패키지.
IC는 제외한다. 모델 판단, PIT 도구, 체결·회계, 평가, 사람용 감사 화면을 분리한다.

**현재 상태:** 로컬 엔진과 대시보드 구현·합성 검증 완료. SSM 실제 읽기 연결 검증.
실제 2년 투자 실험은 미실행이다. 완전한 분배금 이력·KRX 달력·가격 판본 근거·과거 웹
본문 아카이브가 준비되지 않았다. 서비스 v2에서 에이전트 루프와 스키마 변환 경로만
복제했다. 운영 DB writer·배포·fixture 생성은 가져오지 않았다. 출처는 PROVENANCE.json.

## 설치 / 대시보드

이 폴더는 운영 Python 워크스페이스와 분리된 자체 uv workspace/lockfile을 사용한다.

```powershell
cd D:\Github\edge-investment-lab\src\apps\local\investment-lab
uv sync --all-extras --group dev --frozen
uv run python -m investment_lab dashboard --port 8766
```

http://127.0.0.1:8766 에서 확인한다. 외부 바인딩·CORS는 제공하지 않는다.
실험이 없으면 빈 화면이며 성과를 채워 넣지 않는다. 합성 실행은 별도 표기한다.
가설은 화면에서 저장하거나 검증된 데이터셋으로 새 실험을 생성한다. 생성만으로
모델을 호출하지 않는다. 3거래일 실행/재개 버튼은 별도이며 API 키가 필요하다.
브라우저 자동화 연결이 없어 현재 시각 검수는 미실시; HTTP/보안 경계 테스트는 수행했다.

## 클라우드 읽기 연결

```powershell
.\scripts\connect.ps1
```

이 터미널은 터널을 유지한다. 다른 터미널에서 다음을 실행한다.

```powershell
New-Item -ItemType Directory -Force runs/access | Out-Null
Invoke-WebRequest https://truststore.pki.rds.amazonaws.com/ap-northeast-2/ap-northeast-2-bundle.pem -OutFile runs/access/rds-ca.pem
uv run --extra cloud python -m investment_lab.cloud --ca runs/access/rds-ca.pem --out runs/access/db-probe.json --export-start 2024-10-14 --export-end 2026-10-13
```

터널은 기존 `edge-research-ro` 단기 역할과 `EdgeResearchPostgresReadOnlyTunnel`을
사용한다. 원격 대상은 고정된 bastion→RDS:5432, 로컬은 127.0.0.1:15435.
DB 자격증명 조회는 `edge-v2-readonly`의 지정 reader secret만 사용한다.
운영 writer 프로필이나 관리자 프로필을 실행기에 전달하지 않는다.

2026-10-14 검증: TLS, DB 계정 edge_analysis_v2_reader, read_only=on, 테이블
INSERT/UPDATE/DELETE/TRUNCATE 및 스키마 생성·관리자 role 없음. 원격 명령 실행 등은
IAM 시뮬레이션으로 거절을 확인했다. 쓰기 쿼리를 운영 DB에 시험하지 않았다.
권한 확대나 새 클라우드 리소스 생성은 없었다. SECURITY DEFINER 조회 함수 5개가
있어 전체 함수 권한에 대한 안전성은 별도 검토 상태이며 실행기는 호출하지 않는다.

SSM 포트 포워딩은 세션 내용 로그를 제공하지 않으므로 [AWS 문서](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager.html)에 따라
세션 감사와 별도로 로컬 원장에 도구 요청/응답을 저장한다. IAM 시뮬레이션은 실제
DB grants를 대신하지 않는다([AWS](https://docs.aws.amazon.com/IAM/latest/UserGuide/access_policies_testing-policies.html)).

조회된 가격은 `price-export.json`에 **격리된 원자료**로 저장한다. 바로 실행용
데이터셋으로 변환하지 않는다. 현재 1,980행/시가 1,980건이며 price_basis가
290행은 null, 1,690행은 `raw_close;adj_asof=2026-08-02`라 판본 의미 검토가 필요하다.
구성종목은 2026-07-29~2026-10-13의 50일. 현재 reader에 보이는 분배금 테이블 없음.

## 데이터 계약

검토한 파일만 `runs/datasets/<이름>.json`에 놓는다. 이름은 영문/숫자/밑줄/하이픈.
관리자가 등록하는 데이터는 신뢰 경계에 속한다. 입력자가 거짓 시각 증명을 넣는
공격까지 해시로 검증할 수는 없다. 실제 원천과 판본 증거 확인이 먼저다.

필수 최상위 필드:

| 필드 | 의미 |
|---|---|
| ticker / kind | `305720` / `historical` 또는 명시적인 `test_fixture` |
| calendar / calendar_source | 검증된 거래일 배열 / 거래소 달력 출처 |
| price_basis / evidence | `unadjusted` / 가격 판본과 데이터 출처 근거 |
| distribution_coverage | 검토 후 `complete`; 미확인은 실행 거절 |
| bars | 원장용 `{date,open,close,available_at,suspended}` 배열; 모델에 직접 전달하지 않음 |
| distributions | `{id,ex_date,pay_date,amount}` 배열; 주당 금액, 세전 분배금 가정 |
| records | 도구에 제공할 증명된 과거 문서·가격 관측 배열 |

각 record는 `{id,kind,content,url,published_at,available_at,version_at,captured_at,
evidence,sha256}`를 갖는다. sha256은 UTF-8 content의 해시다. components 배열에
요약·첨부·리다이렉트·캐시 내용의 같은 증거 구조를 포함한다. 필수 시각은 timezone
포함 ISO8601. 하나라도 미래/불명/상충이면 전달을 거절한다.

가격 관측은 kind=`price`, content=`{"date":"2025-01-03","open":"100","close":"101"}`
형식이다. 원장 가격과 분리하여 같은 PIT 증명을 통과한 것만 모델 문맥에 넣는다.
자료가 누락되면 모델 입력에서 비어 있고, 원장 거래일 가격이 빠지면 실행을 거절한다.
가격 관측/뉴스를 운영 DB 원자료에서 자동으로 PIT 인증하는 기능은 제공하지 않는다.

`search`와 `read`는 등록된 역사 웹 아카이브를 검색한다. 일반 실시간 웹 검색
provider는 아직 연결하지 않았다. 현재 검색 결과를 날짜만 보고 허용하지 않기 위한
제한이며, 과거 본문 판본을 입증하는 adapter가 확보되어야 연결할 수 있다.

## 실행 / 재개 / 비교

config.example.json의 날짜는 예시다. 마지막 완료 거래일을 확인해 고정하고 비용
bps를 반드시 채운다. 1bp=0.01%. 키는 JSON이나 지시문에 넣지 않고 실행 프로세스의
DEEPSEEK_API_KEY 환경변수로만 제공한다. 프롬프트는 RESEARCH.md를 활용한다.

```powershell
uv run python -m investment_lab create --config my-config.json --dataset runs/datasets/verified.json
uv run --extra agent python -m investment_lab resume runs/<실험ID> --max-days 3
uv run --extra agent python -m investment_lab resume runs/<실험ID>
uv run python -m investment_lab report runs/<실험ID>
```

모델 식별자·연구 지시·코드·lockfile·데이터 해시가 고정된다. 수정하면 새 실행을
만들어야 한다. 공급자가 동일 모델 이름의 내부 버전을 바꾸는 것까지 고정할 수는 없다.
매일 한 SQLite 트랜잭션에 기록하며 실패한 날은 원장 전체가 롤백된다. 모델 재시도는
추가 비용이 들 수 있으나 중복 체결은 없다. UI의 모델 비용은 SDK 추정치이며 제공자가
반환하지 않으면 N/A다. 과금 청구서와 검증된 금액으로 간주하지 않는다.

첫날에는 장 마감 후 판단만 하고 첫 주문은 둘째 거래일 시가에 체결된다.
매수·보유도 같은 시점부터 시작한다. 거래정지일의 주문은 취소하고 그날 다시 판단한다.
최종일 판단은 미체결로 기록하며 강제 청산하지 않는다. 분배금은 배당락일 직전 수량에
발생하고 지급일까지 미수금으로 평가한다. 지급일이 휴장일이면 다음 원장 관측일에
현금화한다. 수익률은 실현+미실현+미수금을 포함한다. 개인별 세금은 포함하지 않는다.

비용 민감도는 새 설정으로 별도 실행한다. `gross`는 같은 판단 경로를 0비용으로
재계산한 회계 비교이며, 무비용 조건에서 모델이 다시 판단한 전략 성과는 아니다.

## 검증

```powershell
uv run --all-extras pytest -q
uv run python -m investment_lab benchmark
node --check src/investment_lab/static/app.js
uv build
```

벤치마크는 합성 계약 사례 190개(미래100·정상30·불명60), 우회6개.
동일 날짜만 바꿔 수를 늘리지 않고 전달 표면과 누수 원인의 조합을 사용한다.
정상 대조군 포함으로 전부 차단하는 도구는 통과하지 못한다. 합성 계약 검증을
실제 웹 전체 차단율이나 미래 지식이 전혀 없는 모델이라는 주장으로 확장하지 않는다.

인증·PIT 게이트·원장은 신뢰된 로컬 제어기에서 실행한다. 연구 모델은 제공된
MCP search/read 도구만 받으며 셸·파일·DB·일반 웹 도구가 없다. 이는 도구 수준
격리이며, 로컬 운영체제 관리자나 수정된 프로그램에 대한 샌드박스는 아니다.

결과는 `runs/` 아래 로컬에만 저장하고 Git에서 제외한다. 데이터 준비와
실제 2년 실행 완료는 대시보드와 검증 기록에서 별도로 판단한다.
