# 분석 지시문 구조

시스템 지침은 harness.md의 작업 수행·완료·도구 경계 규칙이다. 분석 지침은 공통 조사 원칙 → 작업 YAML → 공통 출력·근거 계약 → 작성 원칙 순서로 실행별 AGENTS.md를 조립해 최초 작업 메시지에 전달한다. 로컬과 클라우드 워커는 같은 runner를 사용한다. 옵시디언이나 개발자 PC의 문서를 실행 중 읽지 않는다.

| 문서 | 담당 |
|---|---|
| [harness.md](../src/edge_analysis_v2/prompts/harness.md) | 작업 수행, TODO·메모, 원자료 재조회, 미완료 후속 실행, 도구 경계 |
| [outlook.yaml](../src/edge_analysis_v2/prompts/outlook.yaml) | 현재 가격 기준 1개월 전망, 네 질문, 전체 구성·상위 5종목 심층 범위, 전망 편집·제출 |
| [movement.yaml](../src/edge_analysis_v2/prompts/movement.yaml) | 오늘 중요한 배경과 이전 설명의 변화, 가벼운 조사 범위, 설명 선정·제출 |
| [research.md](../src/edge_analysis_v2/prompts/research.md) | 최우선 종료 조건·재귀 분석 정의, DB 밖 조사, 실행 경계 |
| [output-contract.md](../src/edge_analysis_v2/prompts/output-contract.md) | 최종 근거 연결, 수치·시점 보존, 상세와 요약 일치 |
| [AGENTS.md](../src/edge_analysis_v2/agent/workspace/AGENTS.md) | 결론 우선, 짧은 문장, 구체적인 명사형 소제목, 최대 5불릿 등 작성 원칙 |
| hypothesis-analysis-workflow | 필요할 때 읽는 ACH 기반 조사 방법: 사업 이해 → 같은 증거로 설명 비교 → 모순·반증 → 다음 관측 → 설명 수정 |
| etf-hypothesis-analysis | 필요할 때 읽는 투자 판단 방법: 사건 → 사업 → 이익 → 기대 → 평가 → ETF → 한 달 가격 |
| 코드의 도구 정의·응답 스키마 | 호출 인자·반환·최종 근거 가능 여부·제약, JSON 필드·타입·계층 |

조사 종료 기준과 재귀 분석의 핵심 정의는 research.md에서 관리하고 모델 입력의 맨 앞에 둔다. 스킬은 적용 방법과 예시를 보충한다. 중요한 질문은 근거로 답해 반영했거나, 전제 제거·대안 비교로 결론에 영향 없음을 보였거나, 실행 가능한 대안 뒤에도 구조적 한계가 확인됐을 때 닫는다. 두 스킬에는 종료 기준과 작업별 범위를 복제하지 않는다. 어느 스킬도 읽지 않은 실행에도 공통 기준이 전달된다.

ACH는 경쟁 설명과 불일치 증거를 다루는 영감이다. 고정 가설 수·점수 행렬을 강제하지 않으며, 사업을 이해하기 전 가설을 요구하지 않는다. 조사 결과는 가능한 현재 결론에 반영한다. 조건부 전망만 나열하거나 미래 발표에 현재 조사를 미루지 않는다.

대시보드의 작업별 YAML 버전·비교는 해당 YAML만 대상으로 한다. 공통 문서와 스킬은 지시문 목록에서 현재 패키지 내용을 조회한다. **과거 실행은 system_prompt.txt(하네스), AGENTS.md(조립된 분석 지침), model_input.json(최초 메시지)**으로 확인한다. input.json은 축소 전 원자료이며 workspace.read_source로 재조회할 수 있다. 공통 문서는 Git으로 관리하며 YAML의 버전 번호가 전체 지침의 버전을 뜻하지 않는다. skills.json 해시는 제공 문서 버전 기록이며 실행 적합성 검사가 아니다.

회귀 검증은 두 작업에서 Skill·Read 호출 없이도 공통 문서가 조립된 AGENTS.md와 모델 입력에 포함되는지 확인한다. 기존 출력 스키마·편집·선정·도구 접근 경계도 테스트한다. 코드 테스트 통과는 모델의 조사 품질이나 전망 적중을 보장하지 않는다. 실제 실행에서 도구 기록·근거·남은 공백과 결론을 별도로 검수한다.
