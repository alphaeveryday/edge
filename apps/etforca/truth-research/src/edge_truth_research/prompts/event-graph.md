# 사건 그래프 스킬 (국내 종목)

국내 뉴스·공시에서 뽑은 사건을 담은 그래프다. 이 문서는 무엇이 저장돼 있고 어떻게 찾는지 알려 준다. 숫자는 2026-10-24 운영 그래프 기준이다.

## 1. 저장된 것
- **사건**: 기사·공시 한 건에서 뽑은 '누가 무엇을 했다' 한 줄이다. 타입(6절 목록)·술어(SIGN·ACQUIRE 등)·사건일을 가진다. 2026-01-30 이후 약 9.5만 줄이다.
- **같은 일이 여러 줄**: 기사마다 따로 뽑으므로 한 계약이 보도 수만큼 줄이 된다. 도구가 묶어서 '보도 N건'으로 보여 준다. 보도 수를 일의 수로 읽지 않는다.
- **참여**: 회사가 역할을 맡아 사건에 붙는다(SUPPLIER·CUSTOMER·ACQUIRER·TARGET_COMPANY·ISSUER·PARTNER 등). 같은 계약이라도 공급자인지 고객인지에 따라 뜻이 반대다.
- **수치**: 사건에 붙은 금액·지분율·주식 수·기간이다. 수치 코드·값·단위·기준을 가진다. 값은 기사에서 기계로 뽑았으므로 틀릴 수 있다.
- **근거**: 사건마다 그 사건을 담은 기사·공시가 이어져 있다. 도구가 돌려주는 기사 id 는 `view_news` 로 연다.
- **없는 것**: 사건 쓰레드의 진행 단계는 비어 있다. 테마·루머·주가 반응 자체는 사건이 아니다. 그래프에 없다고 해서 그 일이 없었다는 뜻은 아니다.

## 2. 도구
- `company_events(days)`: 이 종목 회사가 참여한 사건을 최신순으로 묶어 보여 준다. 급등 전부터 이어진 일인지, 같은 소식의 재탕인지, 회사의 역할이 무엇인지 본다.
- `find_events(event_type, metric, op, value, unit, [basis], days)`: 다른 회사의 같은 타입 사건 중 수치 조건에 맞는 선례를 센다. 이번 일이 흔한 크기인지 본다. 조사 중인 회사의 사건은 뺀다.

## 3. 단위·기준 규칙
- **단위는 저장값 그대로 쓴다**: KRW·USD·PCT·COUNT·DAY·YEARS·MONTHS 중 하나다. CURRENCY·PERCENT 같은 이름은 저장값이 아니라서 0건이 된다.
- **값은 원 단위 숫자로 쓴다**: 450억 원은 `45000000000`, 지분 30% 는 `30`(PCT) 이다.
- **같은 수치가 단위별로 따로 있다**: 계약금액은 KRW 와 USD, 계약 기간은 DAY 와 YEARS 로 나뉜다. 필요하면 단위마다 따로 찾는다.
- **기준(basis)은 보통 생략한다**: 저장값 대부분이 UNKNOWN(아래 표의 UNKNOWN 열)이다. 생략하면 기준과 상관없이 찾고, 줄마다 기준을 보여 준다. 연간과 총액을 꼭 갈라야 할 때만 TOTAL·ANNUAL 을 준다.
- **'모름'**: 값이 없거나 불확실하거나(대략·범위, 숫자 추출 실패), 단위가 다르거나, basis 를 줬을 때 기준이 다른 사건은 따로 센다. 안 맞는다는 뜻이 아니다.

## 4. 수치가 차 있는 곳 (최근 180일, 값 20개 이상)
이 표 밖의 (타입·수치·단위)는 값이 거의 없어 `find_events` 가 비게 된다. 중앙값과 상위 10% 는 크기를 가늠하는 데만 쓴다. 이상값이 섞여 있다.

| 타입 | 수치 | 단위 | 값 수 | UNKNOWN | 중앙값 / 상위 10% |
|---|---|---|---|---|---|
| COMPANY.ALLIANCE.PARTNERSHIP | INVESTMENT_COMMITTED | KRW | 198 | 61% | 1,500억 / 2조 |
| COMPANY.CAPITAL.DIVIDEND_DECISION | DIVIDEND_PER_SHARE | KRW | 89 | 98% | 374원 / 1,500원 |
| COMPANY.CAPITAL.DIVIDEND_DECISION | TOTAL_DIVIDEND_VALUE | KRW | 136 | 72% | 30조 / 110조 |
| COMPANY.CAPITAL.EQUITY_ISSUANCE | ISSUE_VALUE | KRW | 364 | 39% | 3,500억 / 2.4조 |
| COMPANY.CAPITAL.IPO | IMPLIED_MCAP | KRW | 23 | 100% | 200조 / 712조 |
| COMPANY.CAPITAL.IPO | OFFER_PRICE | KRW | 53 | 100% | 12,000원 / 19,500원 |
| COMPANY.CAPITAL.IPO | OFFER_VALUE | KRW | 38 | 76% | 2.6조 / 13.9조 |
| COMPANY.CAPITAL.SHARE_BUYBACK | BUYBACK_VALUE | KRW | 644 | 74% | 1,153억 / 40조 |
| COMPANY.CAPITAL.SHARE_BUYBACK | CANCEL_VALUE | KRW | 170 | 43% | 1,000억 / 40조 |
| COMPANY.CAPITAL.SHARE_BUYBACK | SHARES | COUNT | 244 | 95% | 629,327주 / 13,762,500주 |
| COMPANY.COMMERCIAL.MARKET_ENTRY | INVESTMENT_VALUE | KRW | 212 | 70% | 1조 / 20조 |
| COMPANY.COMMERCIAL.MARKET_ENTRY | INVESTMENT_VALUE | USD | 27 | 56% | $15.5B / $72.2B |
| COMPANY.CONTRACT.SIGNING | CONTRACT_DURATION | DAY | 258 | 0% | 842일 / 2,189일 |
| COMPANY.CONTRACT.SIGNING | CONTRACT_DURATION | YEARS | 189 | 94% | 5년 / 20년 |
| COMPANY.CONTRACT.SIGNING | CONTRACT_VALUE | KRW | 3,076 | 44% | 3,855억 / 4.3조 |
| COMPANY.CONTRACT.SIGNING | CONTRACT_VALUE | USD | 96 | 67% | $70M / $20B |
| COMPANY.EARNINGS.GUIDANCE_CHANGE | NEW_VALUE | KRW | 115 | 83% | 1.4조 / 27조 |
| COMPANY.EARNINGS.RESULT_RELEASE | ACTUAL_VALUE | KRW | 4,010 | 98% | 2,963억 / 15조 |
| COMPANY.EARNINGS.RESULT_RELEASE | ACTUAL_VALUE | USD | 25 | 100% | $300M / $28.1B |
| COMPANY.EARNINGS.RESULT_RELEASE | DOC_OP_ACTUAL | KRW | 1,436 | 98% | 1,809억 / 4.1조 |
| COMPANY.EARNINGS.RESULT_RELEASE | DOC_REVENUE_ACTUAL | KRW | 571 | 90% | 1.4조 / 53.7조 |
| COMPANY.FINANCING.DEBT_ISSUANCE | ISSUE_VALUE | KRW | 535 | 12% | 3,000억 / 2조 |
| COMPANY.FINANCING.DEBT_ISSUANCE | ISSUE_VALUE | USD | 48 | 13% | $300M / $500M |
| COMPANY.INVESTMENT.STAKE_ACQUISITION | DEAL_VALUE | KRW | 906 | 55% | 2,000억 / 4.1조 |
| COMPANY.INVESTMENT.STAKE_ACQUISITION | DEAL_VALUE | USD | 84 | 51% | $1B / $1B |
| COMPANY.INVESTMENT.STAKE_ACQUISITION | OWNERSHIP_CHANGE | PCT | 29 | 100% | 13.4% / 70% |
| COMPANY.INVESTMENT.STAKE_ACQUISITION | OWNERSHIP_RATIO | PCT | 327 | 100% | 14.5% / 58.3% |
| COMPANY.LEGAL.LAWSUIT | AWARDED_AMOUNT | KRW | 26 | 50% | 153억 / 270억 |
| COMPANY.LEGAL.LAWSUIT | CLAIM_AMOUNT | KRW | 157 | 99% | 8,300억 / 1조 |
| COMPANY.LEGAL.LAWSUIT | SETTLEMENT_AMOUNT | KRW | 23 | 96% | 7,000억 / 2.5조 |
| COMPANY.LEGAL.REGULATORY_ACTION | PENALTY_AMOUNT | KRW | 424 | 68% | 990억 / 4.6조 |
| COMPANY.M_AND_A.ACQUISITION | DEAL_VALUE | KRW | 452 | 63% | 6,933억 / 2.7조 |
| COMPANY.M_AND_A.ACQUISITION | OWNERSHIP_RATIO | PCT | 167 | 100% | 77% / 100% |
| COMPANY.OWNERSHIP.INSIDER_TRANSACTION | OWNERSHIP_AFTER | PCT | 62 | 100% | 7.2% / 33.6% |
| COMPANY.OWNERSHIP.INSIDER_TRANSACTION | OWNERSHIP_CHANGE | PCT | 52 | 100% | 3.5% / 15% |
| COMPANY.OWNERSHIP.INSIDER_TRANSACTION | SHARES_TRADED | COUNT | 149 | 78% | 118,000주 / 10,140,000주 |
| COMPANY.OWNERSHIP.INSIDER_TRANSACTION | TRADE_VALUE | KRW | 402 | 68% | 282억 / 2.6조 |
| COMPANY.PRODUCTION.CAPACITY_CHANGE | CAPEX_VALUE | KRW | 661 | 77% | 9,700억 / 20조 |
| COMPANY.PRODUCTION.CAPACITY_CHANGE | CAPEX_VALUE | USD | 23 | 78% | $560M / $9B |
| COMPANY.PRODUCT.LAUNCH | INITIAL_ORDER_VALUE | KRW | 116 | 48% | 1,000억 / 3조 |
| POLICY.REGULATION.RULE_CHANGE | PENALTY_CAP_AMOUNT | KRW | 297 | 55% | 85억 / 3.4조 |

## 5. 탐색법
1. **먼저 `company_events(90)`**: 급등일의 소식이 며칠 전부터 이미 보도됐는지(재탕), 회사가 어떤 역할인지, 같은 상대와 이어진 일이 있는지 본다.
2. **급등 사유가 4절 표의 타입이고 수치가 있으면 `find_events`**: 이번 값(또는 그 절반)을 기준값으로 두고 `gte` 로 최근 180일에 이만큼 큰 일이 몇 건 있었는지 센다. 맞음이 많으면 흔한 크기, 0~2건이면 드문 크기다. 이번 값이 작아 보이면 `lte` 로도 본다.
   - 계약 450억: `COMPANY.CONTRACT.SIGNING`, `CONTRACT_VALUE`, gte, `45000000000`, KRW
   - 지분 30% 인수: `COMPANY.M_AND_A.ACQUISITION`, `OWNERSHIP_RATIO`, gte, `30`, PCT
   - 공모 규모: `COMPANY.CAPITAL.IPO`, `OFFER_VALUE`, KRW
   - 자사주 매입: `COMPANY.CAPITAL.SHARE_BUYBACK`, `BUYBACK_VALUE`, KRW
   - 설비 투자: `COMPANY.PRODUCTION.CAPACITY_CHANGE`, `CAPEX_VALUE`, KRW
3. **표 밖의 사유**(정치·루머 테마, 임상 결과, 기술 실증, 단순 기대감)는 수치를 비교하지 않는다. `company_events` 만 본다.
4. **결과는 실마리다**: 근거로 쓰려면 돌려준 기사 id 를 `view_news` 로 열어 `add_evidence` 한다.

## 6. 사건 타입
타입 코드는 '대분류.나머지'다(예: `COMPANY:` 줄의 `CONTRACT.SIGNING` → `COMPANY.CONTRACT.SIGNING`). 사건은 이 타입으로만 저장된다.
