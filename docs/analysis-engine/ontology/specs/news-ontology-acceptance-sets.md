---
doc_type: spec
status: Proposed
owner: engineering
created: 2026-07-22
updated: 2026-07-22
method: skill://ontology-design-criteria
related:
  - news-ontology-criteria.md
  - news-ontology-rulebook.md
---
# 타입 수용셋 (positive5 / hard-negative5) — D1 자매-비중첩 게이트

> **역할:** D1(타입 경계) 게이트의 **적대적 수용시험[B]** 실체화. 통과하는 유일한 길 = 자매타입을 실제로 거절하는 것 → 반-Goodhart.
> **규율(rigor 승계):** 한 타입씩. positive5 전부 채택 + hard-negative5 전부 거절해야 타입 확정.
> **근거:** 예시는 v3 코퍼스 실제 제목. 경계 규칙은 `ontology_ref.txt` 명시 조항.

## 클러스터 A — DEAL 계열 (최고빈도·최고혼동)

### COMPANY.CONTRACT.SIGNING
- **변별 신호:** 고객-공급자 간 **재화/용역 납품·수주·공급계약**(SUPPLIER→CONTRACT_OBJECT). 지분·합병·동등제휴 아님.
- **positive5:** `에이직랜드, SK하이닉스와 318억 주문형 반도체 설계 계약` · `스맥, 93억 공작기계 공급계약` · `비에이치아이, 1883억 LNG 발전설비 공급계약` · `삼성重, 원유운반선 2척 수주` · `효성重, 호주서 3100억원 수주`
- **hard-negative5 (거절):** `포스코-KB국민은행 공급망 금융 지원`→PARTNERSHIP(동등제휴) · `코스맥스…가톨릭대 산학협력 체결`→PARTNERSHIP("체결"이나 제휴) · `파마리서치, 美 제조사 인수`→M&A(경영권) · `삼성 라이프사이언스 펀드 조성`→STAKE(투자) · `휴온스 합병`→MERGER
- **경계 주의:** `한화오션 7.8조 KDDX 우협 선정` = 타입 CONTRACT.SIGNING이나 **stage=PREFERRED_BIDDER**(체결 아님) — 타입 통과, 단계로 구분.

### COMPANY.ALLIANCE.PARTNERSHIP
- **변별 신호:** **동등 주체 간 협력·제휴·MOU·JV.** 금전-재화 교환 없음, 지분·경영권 이전 없음, 법적 결합 없음.
- **positive5:** `포스코-KB국민은행 공급망 금융 지원` · `코스맥스…가톨릭대 산학협력 체결` · `네이버·엔비디아 AI 팩토리 파트너십` · `한·이탈리아 중기 협력 협약 체결` · `네오크레마, 바이오텍시아와 협력 확대`
- **hard-negative5 (거절):** `스맥 공작기계 공급계약`→CONTRACT(공급) · `파마리서치 인수`→M&A · `SK가스 울산GPS 지분 매각`→STAKE · `휴온스 합병`→MERGER · `미래에셋-코빗 기업결합 승인`→MERGER
- **경계 주의:** "체결"·"협약"은 CONTRACT와 공유 트리거 → 주체 관계(동등 vs 고객-공급)로 변별.

### COMPANY.M_AND_A.ACQUISITION
- **변별 신호:** 한 주체가 대상의 **경영권(과반·지배력) 취득**(인수/품는다/상폐). 소수지분 아님, 합병(법적 결합) 아님.
- **positive5:** `파마리서치, 美 화장품 제조사 인수` · `맥쿼리, 가비아 품는다…6300억 상폐 추진` · `흥국화재, 예별손보 인수 추진` · `클로봇, 두산로지스틱스솔루션 인수` · `앱튼, 지피씨알 인수 추진`
- **hard-negative5 (거절):** `삼성 펀드 조성`→STAKE(비지배 투자) · `SK가스 울산GPS 지분 매각`→STAKE(지분 거래·비지배) · `휴온스 합병`→MERGER(결합, 양사 존속 아님) · `포스코-KB 협력`→PARTNERSHIP · `에이직랜드 설계 계약`→CONTRACT
- **경계 주의:** ACQUISITION(지배력 취득, 인수사 존속) vs MERGER(법적 1개로 결합)가 최난 경계.

### COMPANY.INVESTMENT.STAKE_ACQUISITION
- **변별 신호:** **비지배 소수 지분/투자/펀드.** 경영권 취득 아님, 자기자본거래 아님.
- **positive5:** `삼성 라이프사이언스 펀드 3호 조성` · `국민성장펀드, 피지컬 AI에 16조 투입` · `부산 미래산업펀드…디알액시온에 150억` · `삼성, 바이오 벤처 투자 펀드 조성` · `경북도, 1500억 산림개발 투자 유치`
- **hard-negative5 (거절):** `파마리서치 인수`→M&A(지배) · `맥쿼리 가비아 품는다`→M&A(지배+상폐) · `휴온스 합병`→MERGER · `포스코-KB 협력`→PARTNERSHIP · `스맥 공급계약`→CONTRACT
- **경계 결함 발견:** `삼성, 호남·충청·영남에 1000조 투자`·`삼성, 영남권 60조 베팅`은 현행 STAKE_ACQUISITION(n=1,849)에 혼입돼 있으나 실제는 **설비 capex(PRODUCTION.CAPACITY_CHANGE)** — 지분투자 아님. → STAKE는 "지분/equity"로 좁히고 capex 분리(차기 타입 정제).

### COMPANY.M_AND_A.MERGER
- **변별 신호:** 둘 이상 법인이 **하나로 결합**(합병/흡수합병/주식교환/기업결합).
- **positive5:** `휴온스글로벌, 휴온스·휴온스랩 합병` · `애경산업, 자회사 원씽 합병` · `알테오젠, 자회사 흡수합병` · `미래에셋-코빗, 기업결합 승인` · `동양생명, 포괄적 주식교환`
- **hard-negative5 (거절):** `파마리서치 인수`→M&A.ACQUISITION(지배취득, 결합 아님) · `삼성 펀드 조성`→STAKE · `포스코-KB 협력`→PARTNERSHIP · `스맥 공급계약`→CONTRACT · `현대차 상생 강화`→PARTNERSHIP
- **경계 주의:** `롯데시네마-메가박스 합병, 없던 일로` = 타입 MERGER, **stage=CANCELLED**(무산도 타입은 유지).

## 클러스터 B — 실적·자본 계열

### COMPANY.EARNINGS.RESULT_RELEASE
- **변별 신호:** **확정 실적**(영업익/순익/매출 실현치, 분기·연간 결과). 전망 아님, 주주환원 아님.
- **positive5:** `삼성전자 2Q 영업익 89.4조` · `셀트리온, 2분기 영업익 4300억…전년비 77.3%↑` · `글로벌세아 제지…5월 누계 영업익 100%↑` · `대웅제약, 나보타 누적 매출 1조 달성` · `삼성 시스템LSI, 1분기 역대 최대 매출`
- **hard-negative5 (거절):** `LG이노텍 5년내 영업익 1조 목표`→GUIDANCE(전망) · `KCC글라스 현금배당`→DIVIDEND · `셀트리온 1000억 자사주 소각`→BUYBACK · `SK하이닉스 45조 ADR 발행`→EQUITY_ISSUANCE · `셀트리온 지속가능경영보고서 발간`→**보고서 발간(실적 아님)**
- **경계 결함 발견:** ESG·지속가능경영보고서 발간 다수가 RESULT_RELEASE에 오분류(셀트리온·유한양행·LG엔솔·기아 등) → 보고서 발간은 실적결과 아님, 분리.

### COMPANY.EARNINGS.GUIDANCE_CHANGE
- **변별 신호:** **미래 목표·전망 제시/상향/하향**(수주목표·매출목표). Forward-looking.
- **positive5:** `HD현대일렉, 올해 수주 목표 22.8%↑` · `HD현대일렉, 수주 목표 51.85억달러 상향` · `LG이노텍 5년내 영업익 1조 목표` · `폴레드 유아사업 매출 내년 2배로` · `이스트소프트 연간 수주 100억 초과 예고`
- **hard-negative5 (거절):** `삼성전자 2Q 영업익 89.4조`→RESULT(확정) · `KCC글라스 현금배당`→DIVIDEND · `자사주 소각`→BUYBACK · `유상증자`→EQUITY · `회사채 발행`→DEBT

### COMPANY.CAPITAL.DIVIDEND_DECISION
- **변별 신호:** **배당 결정**(현금/중간/특별배당, 주당 X원).
- **positive5:** `KCC글라스, 주당 600원 현금배당 결정` · `하이로닉, 주당 233원 현금배당` · `코나아이 분기배당 주당 800원` · `케어젠 중간배당 1주당 200원` · `그래디언트 특별배당 추진`
- **hard-negative5 (거절):** `셀트리온 자사주 소각`→BUYBACK(주주환원이나 별개) · `SK하이닉스 ADR 발행`→EQUITY · `삼성전자 2Q 영업익`→RESULT · `회사채 발행`→DEBT · `현대차 상생 강화`→PARTNERSHIP

### COMPANY.CAPITAL.SHARE_BUYBACK
- **변별 신호:** **자사주 취득·소각·신탁**(주식 수 감소 방향).
- **positive5:** `아이패밀리에스씨 자사주 90억 소각` · `셀트리온 1000억 자사주 소각` · `대동금속 자사주 취득·소각` · `원텍 자기주식 취득 신탁계약` · `남양유업 220억 자사주 소각`
- **hard-negative5 (거절):** `KCC글라스 현금배당`→DIVIDEND · `에코프로비엠 1.2조 유상증자`→EQUITY(**발행=반대방향**) · `삼성전자 영업익`→RESULT · `신종자본증권 발행`→DEBT · `가비아 품는다`→M&A

### COMPANY.CAPITAL.EQUITY_ISSUANCE ↔ COMPANY.FINANCING.DEBT_ISSUANCE
- **EQUITY 신호:** 주식 발행(유상/무상증자·제3자배정·ADR). **DEBT 신호:** 채권/사채 발행(회사채·신종자본증권·CB·외화채권).
- **EQUITY positive5:** `SK하이닉스 45조 ADR 발행` · `에코프로비엠 1.2조 유상증자` · `헝셩그룹 168억 제3자배정 유상증자` · `애드바이오텍 133억 유증 납입` · `유진테크놀로지 무상증자`
- **DEBT positive5:** `DB증권 신종자본증권 1500억 발행` · `우리은행 외화채권 직접 발행` · `카드·캐피탈 위안화 채권 조달` · `LSK아이로봇 8회차 CB` · `회사채 발행(롯데쇼핑·대한항공)`
- **상호 hard-negative:** EQUITY는 채권발행 거절(DEBT), DEBT는 유상증자 거절(EQUITY); 둘 다 `자사주 소각`(BUYBACK)·`현금배당`(DIVIDEND) 거절.
- **경계 결함 발견:** DEBT_ISSUANCE(n=1,010)에 `프로메디우스 215억 투자 유치`(equity received→STAKE)·`신보 1200억 우대보증`(대출/보증 지원, 발행 아님) 다수 오분류 → "회사가 채권을 발행"으로 좁혀야.

## 클러스터 C — 정책·법률 계열 (핵심: 법원↔규제기관↔사인 3자 변별)

### POLICY.REGULATION.RULE_CHANGE
- **변별 신호:** 정부·규제기관이 **규칙·법·제도**를 신설/개정/완화/강화(일반 대상). 특정기업 제재 아님·법원 판결 아님·관세 아님.
- **positive5:** `주가조작 원금 몰수 처벌 대상 확대` · `단일종목 레버리지 투자문턱 상향(현금 3000만)` · `ESG 공시 대상 자산 30조→10조 확대` · `복지부, 첨생법 자가세포 위험도 하향` · `반도체특별법·납품단가 지원 확대`
- **hard-negative5 (거절):** `공정위, 다보메그네틱 제재`→REGULATORY_ACTION(특정기업) · `법원, 카카오 과징금 취소`→COURT.RULING · `美 타이어 덤핑마진`→TARIFF · `美 中기업 블랙리스트`→SANCTION · `LS증권 소송 종결`→LAWSUIT

### POLICY.TRADE.TARIFF_CHANGE
- **변별 신호:** 관세·무역조치(관세 부과/인하·보조금·FTA·덤핑마진·수입면세).
- **positive5:** `美상무부 한국산 타이어 덤핑마진 8~13%` · `BYD 전기차 보조금 못 받는다` · `미얀마 연료·비료 수입 면세 연장` · `한·세르비아 CPEA(FTA) 타결` · `포스코·현대제철 美 관세 부담 완화`
- **hard-negative5 (거절):** `美 中기업 블랙리스트`→SANCTION(제재≠관세) · `주가조작 처벌 확대`→REGULATION · `법원 과징금 취소`→COURT · `공정위 제재`→REGULATORY_ACTION · `소송 종결`→LAWSUIT

### POLICY.SANCTION.IMPOSITION
- **변별 신호:** 특정 대상 **제재**(블랙리스트·경계기업 지정·금융/원유 제재·수출규제) + TARGET.
- **positive5:** `美, 中기업 188곳 블랙리스트 지정` · `미·걸프, 헤즈볼라 금융기관·간부 제재` · `美, 이란산 원유 제재면제 취소` · `미, 이란 LPG 위장 네트워크 제재` · `美상무, 앤스로픽 AI 수출규제 처벌 경고`
- **hard-negative5 (거절):** `美 타이어 덤핑마진`→TARIFF(무역구제) · `주가조작 처벌 확대`→REGULATION · `법원 판결`→COURT · `공정위 과징금`→REGULATORY_ACTION · `한·세르비아 FTA`→TARIFF

### POLICY.COURT.RULING ↔ COMPANY.LEGAL.LAWSUIT ↔ COMPANY.LEGAL.REGULATORY_ACTION
- **변별 축 = 판정 주체:** **법원**(COURT.RULING) vs **규제기관**(REGULATORY_ACTION) vs **소송 당사자/제기·화해**(LAWSUIT).
- **COURT.RULING pos5:** `법원, 카카오 과징금 취소` · `대법, 포스코 불법파견 인정` · `법원, 고려아연 영풍 의결권 제한 위법` · `대법, 상한초과 중개수수료 비용처리 불가` · `법원, 삼성→하이닉스 이직 1년6개월 금지`
- **REGULATORY_ACTION pos5:** `공정위, 다보메그네틱 제재` · `금감원, NH·한양證 불완전판매 조사` · `영풍 205억·고려아연 84억 과징금` · `테무 IP 모니터링 확대(당국 압박)` · `경기도, 착한기업 인증 실태 조사`
- **LAWSUIT pos5:** `LS증권-현대차증권 ABCP 소송 종결·화해권고` · `테슬라 차주 소송` · `금양 상장폐지 가처분 심문` · `한화솔루션 주주대표 소송성 요구` · `CERCG ABCP 소송`
- **상호 hard-negative:** 과징금 **부과**=REGULATORY_ACTION vs 과징금 **취소(법원)**=COURT.RULING; 소송 **제기**=LAWSUIT vs **판결**=COURT.RULING. COURT는 규제기관제재·소송제기 거절; REGULATORY_ACTION은 법원판결·일반규칙(RULE_CHANGE) 거절.
- **경계 결함 발견:** LAWSUIT(n=492)에 `스타벅스 5·18 조롱`·`보험 무효 통보`·`주주 단체행동` 등 **소송 아닌 분쟁/불만/행동** 오분류 → "정식 소송 제기·진행·판결전 단계"로 좁혀야.

## 클러스터 D — 산업·거시 계열

### INDUSTRY.PRICE.COMMODITY_PRICE_CHANGE
- **변별 신호:** 특정 원자재/상품·관리가격의 **앵커된 가격 변동**: (i) 관리가격의 이산 변경·결정(요금·할증료·공급가, 시행시점 명시) 또는 (ii) 명시된 관측창의 새 프린트(전일比/전주比/전월比/'N주 연속'/특정일 마감 + 구체 수치·지표명). 총물가지수 아님, **누적 회고·마일스톤 단독·영향 해설(시황) 아님**. **절대 배제(앵커 규칙에 우선, v3):** 부동산 시세(아파트·단지·지역 집값), 개인 신용점수, 주가·시총·지수 등락 자체는 **관측창이 있어도** 이벤트가 아니다 → doc_class=MARKET_COMMENTARY. 대상은 원자재·에너지·운임·농산물·금속 등 **산업 투입재 시장가격뿐**이다.
- **positive5:** `국제유가 80달러 밑으로 뚝`(특정일 마감) · `WTI 3.92%↓`(일일 프린트) · `7월 국제선 유류할증료 27단계→19단계`(관리가격 단계 변경) · `E1, 6월 LPG 공급가격 30원 인상`(월별 공급가 결정) · `국내 주유소 휘발유·경유 4주 연속 하락`(명시 창 프린트)
- **hard-negative5 (자매타입 거절):** `5월 소비자물가 3.1%↑`→INFLATION(총지수) · `美 PCE 최고`→INFLATION · `OPEC+ 원유 증산`→SUPPLY.CAPACITY · `美 전략비축유 재고 최저`→INVENTORY · `한은 금리 인상`→MONETARY
- **hard-negative (비이벤트 명시 분류, prompt v3-docclass):** `메모리 가격 2배 폭등…`(누적 회고, 창 없음) · `계란 한판 가격 고공행진`(추세 해설) · `오이·애호박 값 반토막`(누적 회고) · `컨테이너 운임 1년 만에 최고치 도달`(마일스톤 단독) → doc_class=MARKET_COMMENTARY. `"내 신용은?" 700점 밑으로`·`동탄 아파트 2주새 4% 급등`(**절대 배제 — 관측창 유무 불문**) → doc_class=MARKET_COMMENTARY, 어떤 타입에도 배정 금지.
- **경계 결함 발견(v2 정정, 2026-07-24 사용자 판정):** 종전 positive의 `메모리 2배 폭등`·`오이·애호박 반토막`·`몰리브덴 3년 만에 최고`는 **앵커 창 없는 누적 회고/마일스톤 = 시황**으로 재판정. 게이트 프롬프트 `v2-pricegate`로 명문화, 6월 해당 타입 40건 `--refresh-docs` 표적 재추출로 재처리(변경 영향 범위만).
- **v3 승격(2026-07-24 사용자 판정 2건):** 비이벤트는 item 부재가 아니라 **명시 doc_class**(MARKET_COMMENTARY/OPINION_OR_ANALYSIS/PROMOTIONAL/LIST)로 기록하고, 위 배제 문구를 **절대 배제(앵커 규칙에 우선)**로 승격. item 없는 EVENT는 추출실패(`EXTRACTION_INCOMPLETE`)이지 비이벤트가 아니다. 수용 기준: 동탄 아파트 시세 문서(01100801.20260619003306001)는 MARKET_COMMENTARY.

### INDUSTRY.SUPPLY.CAPACITY_CHANGE ↔ INDUSTRY.SUPPLY.INVENTORY_CHANGE
- **CAPACITY 신호:** 공급능력·생산·수주점유·도입확대. **INVENTORY 신호:** 재고 수준 변화.
- **CAPACITY positive5:** `OPEC+ 원유 증산 합의` · `韓조선, 전세계 선박 44% 수주` · `캐나다산 원유 도입 3배 확대` · `건설용 철선 공급 올스톱` · `정부 도시광산 희토류`
- **INVENTORY positive5:** `美 전략비축유 재고 43년만 최저` · `글로벌 원유 재고 日 380만배럴↓` · `석유공사 원유 154만배럴 반입` · `중국산 열연 수입 9배` · `수입 신선란 2억개 공급`
- **상호/외부 hard-negative:** 유가"값"→PRICE · 수요특수→DEMAND · 금리→MONETARY.

### INDUSTRY.DEMAND.DEMAND_CHANGE
- **변별 신호:** 제품/서비스 **수요** 변화(특수·판매 급감·소비 이동).
- **positive5:** `유통업계 냉방가전 특수` · `한국 차 내수 판매 14.3% 급감` · `LG·웰템 냉방 수출 급증` · `1인가구 예적금→주식 ETF` · `벤처 경기실적지수 106.9 첫 돌파`
- **hard-negative5 (거절):** `메모리 가격 폭등`→PRICE · `OPEC 증산`→SUPPLY · `소비자물가 3.1%↑`→INFLATION · `한은 금리 인상`→MONETARY · `고용 17만 증가`→EMPLOYMENT

### MACRO.MONETARY.POLICY_RATE_DECISION ↔ MACRO.FX.EXCHANGE_RATE_POLICY
- **MONETARY 신호:** 중앙은행 **기준금리 결정**(인상/인하/동결). **FX 신호:** 외환당국 환율 개입/정책.
- **MONETARY positive5:** `한은, 3년6개월만 기준금리 2.75%` · `연준, 금리 동결…인하 없다` · `BOJ, 31년만 최고 금리` · `일본 기준금리 0.25%p 인상` · `신현송, 금리 인상 기정사실화`
- **FX positive(별개):** `외환당국 두번째 구두개입` · `외환시장 24시간 개장` · `외환당국 NDF 투기거래 강력대응`
- **hard-negative5 (거절 for MONETARY):** `소비자물가 3.1%↑`→INFLATION · `1분기 성장률 1.8%`→GDP · `외환 구두개입`→FX · `유가 급락`→PRICE · `한은 금 투자`→(자산운용, 정책결정 아님)

### MACRO 데이터-릴리스 family — INFLATION / GDP / EMPLOYMENT (지표로 변별)
- **공통 신호:** 공식 거시지표 발표. **INFLATION**=CPI/PCE 물가지수 · **GDP**=성장률/경상수지 · **EMPLOYMENT**=고용/실업률.
- **INFLATION pos:** `5월 소비자물가 3.1%↑` · `美 PCE 3년여만 최고` · `美 5월 CPI 4.2%↑`
- **GDP pos:** `1분기 성장률 1.8% 상향` · `5월 경상수지 386억달러 흑자` · `명목성장률 10.5%`
- **EMPLOYMENT pos:** `미 5월 고용 17만2천 증가·실업률 4.3%` · `17개월만 취업자 감소` · `대기업 고용 급제동`
- **상호 hard-negative:** 각 지표는 타 지표·개별상품가격(PRICE)·금리(MONETARY) 거절.
- **경계 결함 발견:** DATA_RELEASE 3종에 **사회/세금 통계 오분류** — EMPLOYMENT에 `9급 공채 합격`·`육아휴직 증가`·`남성 전업주부 27만`; INFLATION에 `서울 아파트 재산세 18.7%`; GDP에 `경제총조사 실시`·`국가경쟁력 순위`. → **공식 거시지표(CPI/PCE·GDP·고용통계)로 한정**, 임의 %/사회통계 배제.

## 클러스터 E — 잔여 최고빈도 COMPANY 계열

### COMPANY.PRODUCT.LAUNCH ↔ CERTIFICATION ↔ COMMERCIAL.MARKET_ENTRY
- **LAUNCH:** 신제품/서비스 **출시·개발·출범**. `동국제약 마이팝 다이소 출시` · `한샘 인테리어 플래너 출시` · `삼성전기 유리기판 소재 개발` · `카카오뱅크 AI 사기탐지 도입` · `파워넷 전원장치 출범`
- **CERTIFICATION:** 인증·승인·지정 **획득**. `셀트리온 트룩시마 상호교환성 획득` · `LG디스플레이 ASPICE 획득` · `에이비엘바이오 FDA 패스트트랙` · `한온시스템 포드 Q1 인증` · `K무인기 엔진 국산화`
- **MARKET_ENTRY:** 신시장/지역 **진입·입점·사무소**. `삼성바이오 암스테르담 사무소` · `K패션 싱가포르·베트남 정조준` · `미샤 英 부츠 입점` · `대우건설 이란 재진출` · `닥터그루트 세포라 북미`
- **상호 hard-negative:** 출시↔인증↔진입은 서로 거절; 셋 다 `합작공장 가동`(PRODUCTION)·`대표 내정`(EXECUTIVE)·`분사`(SPINOFF) 거절.

### COMPANY.MANAGEMENT.EXECUTIVE_CHANGE
- **신호:** 경영진 선임/교체/영입/내정/사임.
- **positive5:** `정용진, 이마트 대표 내정` · `롯데하이마트 신임 대표 김종윤` · `NH투자 각자대표 체제 전환` · `KB금융 차기 회장 선임절차` · `카카오게임즈 경영진 교체`
- **hard-negative5 (거절):** `LG화학 엔지니어상 수상`→**(수상, 변동 아님)** · `회장 자사주 매입`→INSIDER · `대표 소송`→LAWSUIT · `분사`→SPINOFF · `제품 출시`→LAUNCH

### COMPANY.PRODUCTION.CAPACITY_CHANGE (capex 흡수 — cluster A 결함 해소)
- **신호:** 생산능력·공장·설비 변화(가동·양산·증설·재건축·**설비 capex**).
- **positive5:** `SK온·현대차 배터리 합작공장 가동` · `LG엔솔·혼다 배터리셀 양산` · `최태원 생산능력 2배 확대` · `기아 스포티지 생산 개시` · `삼성·SK·셀트리온 충청 392조 투자(설비 capex)`
- **hard-negative5 (거절):** `삼성 라이프사이언스 펀드`→STAKE(지분투자≠capex) · `제품 출시`→LAUNCH · `인증 획득`→CERTIFICATION · `유럽 사무소`→MARKET_ENTRY · `온실가스 15% 감축`→**(ESG, 홈 부재)**
- **결함 해소:** cluster A의 "삼성 1000조 투자"(capex)는 STAKE 아니라 **여기**.

### COMPANY.OWNERSHIP.INSIDER_TRANSACTION ↔ CAPITAL.SHARE_BUYBACK (개인 vs 회사)
- **변별 축:** 거래 주체가 **오너/임원 개인**=INSIDER vs **회사 법인**=BUYBACK.
- **INSIDER positive5:** `김정수 회장, 자녀에 주식 증여` · `곽동신 회장 자사주 50억 추가 매입` · `스맥 최영섭 회장 9만주 매수` · `한미약품 지분 이동 경영권 긴장` · `일양약품 오너家 증여`
- **hard-negative5 (거절):** `셀트리온 1000억 자사주 소각`→BUYBACK(회사) · `유상증자`→EQUITY · `가비아 품는다`→M&A · `대표 내정`→EXECUTIVE · `현금배당`→DIVIDEND

### COMPANY.RESTRUCTURING.SPINOFF · COMMERCIAL.PRICING_ACTION
- **SPINOFF:** 분사·사업통합/개편·철수·자산매각. `현대차 사내스타트업 3곳 분사` · `콘테라파마 분사` · `기아 버스사업 접기` · `CJ제일제당 사업구조 개편` · `HMG건설기술연구원 통합`
- **PRICING_ACTION:** **개별 기업**의 가격/금리 조정. `국민은행 주담대 금리 0.2%p 인상` · `하나카드 카드론 12% 상한` · `수박 9990원 반값` · `카카오 상품권 95% 환불` · `은행권 신용대출 조이기`
- **PRICING 3자 변별:** 기업 가격결정(PRICING_ACTION) vs 시장 원자재값(INDUSTRY.PRICE) vs 중앙은행 기준금리(MONETARY). hard-neg: `국제유가 급락`→PRICE · `한은 금리 인상`→MONETARY.

> **신규 결함 발견 #7 (ESG 홈 부재):** `온실가스 15% 감축`·`지속가능경영보고서 발간` 류가 CAPACITY/RESULT로 누출 — 현행 53타입에 ESG 이벤트 타입 없음 → 신설 검토(evolution 채널).

## 클러스터 F — MARKET_INFO · MARKET_STRUCTURE · EXOGENOUS

### MARKET_INFO — ANALYST.RATING_CHANGE ↔ ANALYST.TARGET_PRICE_CHANGE ↔ CREDIT.RATING_CHANGE
- **RATING(투자의견):** `모두 매수 외칠 때 등장한 중립 리포트` · `UBS, 하이닉스 ADR 사고 한국주식 팔아라` · `고려신용정보 Not Rated` · `현대차 중립 리포트` · `에스비비테크 Not Rated`
- **TARGET_PRICE(목표주가):** `롯데쇼핑 목표가 240,000원` · `삼성물산 목표가 59만원 상향` · `신세계 목표가 940,000원` · `삼성전기 목표가 175만 상향` · `한화에어로 목표가 172만 하향`
- **CREDIT(신용등급):** `HD건설기계 신용등급 줄상향` · `DL이앤씨 AA- 안정적 유지` · `한양증권 A 안정적 유지`
- **변별:** 증권사 투자의견(매수/중립) vs 목표주가(숫자) vs 신용평가사 등급(AA/A). 상호 거절 + `삼성전자 영업익`(RESULT)·`배당`(DIVIDEND) 거절. **결함:** CREDIT에 `브랜드평판 1위` 노이즈.

### MARKET_STRUCTURE — INDEX.INCLUSION ↔ EXCLUSION ↔ TRADING_HALT ↔ EXCHANGE_OUTAGE
- **INCLUSION:** `KODEX AI반도체 SK스퀘어 신규 편입` · `SOL ETF 리밸런싱 원익IPS 편입` · `위믹스 크라켄 입성` · `HANARO K-반도체 편입`
- **EXCLUSION:** `한투운용 ETF 4종 상장폐지` · `MSCI 선진지수 불발` · `삼천당제약 바이오 ETF서 제외` · `SK하이닉스 레버리지 ETF 투자유의종목`
- **TRADING_HALT:** `코스피 서킷브레이커·사이드카 발동` · `매도 사이드카` · `제이케이시냅스 거래정지 해제` · `코스닥 매수 사이드카`
- **hard-negative:** `레버리지 예탁금 3천만원 상향`→REGULATION(규칙, 정지 아님) · 편입↔제외 상호 거절.
- **결함 발견 #8:** EXCHANGE_OUTAGE(n=23) **참 양성 거의 없음** — `외환시장 24시간 개장`(구조변경)·`공시오류 배상`(보상)이 대부분 → 정의를 **거래소 시스템 장애**로 한정, 실인스턴스 희소성 재검토.

### EXOGENOUS — 원인으로 변별 (DISASTER/CONFLICT/HEALTH/ACCIDENT/CYBER)
- **DISASTER(자연재해):** `경기·인천 폭우 피해` · `폭염·정전 한전 비상` · `낙동강 녹조 확산` · `김포 공장 침수` · `집중호우 보험 비상`
- **CONFLICT(전쟁/분쟁):** `중동전쟁 장기화` · `美 이란 상선공격 보복공습` · `호르무즈 해협 봉쇄` · `트럼프, 이란 종전합의 위태`
- **HEALTH(병원체):** `美 기생충 7000명 감염·양상추 리콜` · `구제역 SAT1형 백신 접종` · `텍사스 나사벌레 출현`
- **ACCIDENT(물리사고):** `SK하이닉스 청주공장 화재 3600명 대피` · `한화에어로 대전공장 폭발화재 6명` · `동국제강 인천공장 화재` · `현대차 첸나이 공장 화재 후 재개`
- **CYBER(전산/사이버):** `우리은행 고객정보 1.7만건 유출` · `키움증권 전산오류 반대매매` · `보이스피싱 주의보` · `건설공제조합 사이버공격 대비 훈련`
- **변별:** 원인축(자연재해/전쟁/병원체/물리사고/사이버). 최근접 경계 = ACCIDENT(화재·폭발) vs CYBER(전산·유출).

## 클러스터 G — 데이터기반 신규타입 후보 (골드 `OTHER:` 이스케이프 896건 triage)

> 근거: 골드 스캔(§gold-spec) — 주석자가 기존 53타입에 없어 `OTHER:`로 이스케이프한 실제 사건. 빈도 = 진짜 gap 크기. **빈도≠가치**: 저신호는 doc_class 흡수(신설 시 오탐만 증가).

### 신규타입 권고 (genuine gap · pos=실제 OTHER 제목 / hard-neg=오흡수 금지)
- **COMPANY.MANAGEMENT.COMPENSATION_CHANGE** (22): 임원 보수 결정/변경. pos: `Musk new pay package`·`Dimon $36m pay`·`Nadella pay $96.5M`. hard-neg: 경영진 선임→EXECUTIVE_CHANGE · 배당→DIVIDEND.
- **COMPANY.CAPITAL.CAPEX_PLAN** (19): 대규모 설비/인프라 투자 계획 — **capex 결함(#1)의 정식 홈**. pos: `Apple $500B US investment`·`Microsoft $2.1B AI infra`·`TikTok $7B AI push`. hard-neg: 지분투자→STAKE · 가동/증설→PRODUCTION.CAPACITY_CHANGE.
- **COMPANY.GOVERNANCE.SHAREHOLDER_PROPOSAL** (17): 주주제안/주총 표결. pos: `Berkshire shareholder AI committee`·`Deere reject anti-DEI`·`Apple DEI vote`. hard-neg: 배당/자사주→CAPITAL · 경영진→EXECUTIVE_CHANGE.
- **COMPANY.RESTRUCTURING.STRUCTURE_CHANGE** (14): 지배구조/영리전환/보고체계. pos: `OpenAI for-profit move`·`Microsoft reporting revamp`. hard-neg: 분사→SPINOFF · 합병→MERGER.
- **COMPANY.COMMERCIAL.SALES_SUSPENSION** (13): 제품 판매 중단. pos: `Apple halts Watch US sales over patent`. hard-neg: 생산중단→PRODUCTION · 소송(원인)→LAWSUIT.
- **COMPANY.PRODUCT.TEST_RESULT** (11): 제품 시험/실증 결과(방산/임상). pos: `RTX PhantomStrike first flight test`·`live-fire test success`. hard-neg: 인증획득→CERTIFICATION · 출시→LAUNCH.
- **(niche) BANK_STRESS_TEST** (10): 규제 스트레스테스트 결과. pos: `US banks ace Fed health checks`. → 신설 or REGULATORY_ACTION 협소 흡수 검토. hard-neg: 제재/과징금→REGULATORY_ACTION.

### doc_class 흡수 권고 (저신호 — EVENT 승격 금지)
- **AWARD (72, 최다):** PR성 수상/선정(`Recognized as Winner`·`Named Finalist`) → **doc_class=PROMOTIONAL**. 빈도 최다지만 시장신호 부재 → 신규타입 신설 시 오탐만 증가.
- **MILESTONE-시총 (43 다수):** `$5T market cap` 등 가격파생 → **NO_EVENT_MARKET_COMMENTARY**(운영 flight-hours 마일스톤도 저신호).
- **DISPUTE (10):** `Microsoft blames Delta` 등 PR 대응 → 대부분 commentary; 정식 분쟁만 LAWSUIT 인접.

### 편입 조건 (반-Goodhart)
각 신규타입: positive5/hard-negative5 완비 → 6파일 추가(count↑·테스트 갱신) → **재학습**해야 emit(**inert-until-retrain**, §gold-spec). doc_class 흡수 후보는 신설 금지(빈도≠가치).

## 반-Goodhart
- 게이트 = **positive5 전부 수용 ∧ hard-negative5 전부 거절.** hard-neg는 실제 자매타입 코퍼스 예시 → 통과하려면 경계를 진짜로 구분해야 함(다수결·오라클추종으론 불가).
- 자연표본 type_agree는 진단(오라클 오류 가능) — 게이트는 이 수용셋.

## 상태 / 다음
- **완료:** A–F ~49/53 타입 게이트 + **G. 데이터기반 신규타입 triage**(OTHER 896 → 신규 7 권고·doc_class 흡수 3). 경계결함 9건(가격 시황경계 v2 포함) + 정정 3연쇄(§gold-spec).
- **다음:** 신규타입 6파일 심의 반영 + 타입-모델 **재학습**(hard-neg 증강, §gold-spec) — 모두 gated·검증 동반.
