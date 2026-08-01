---
doc_type: research
status: Active
owner: analysis-engine
created: 2026-07-31
related:
  - ../architecture/causal-design-harness.md
  - ../architecture/causal-attribution-p0p9.drawio
---

# 단일 사건 인과귀속 — 문헌 대조와 P0–P9 재설계

> **물음.** Flash Crash 공식 조사보고서 수준의 단일 사건 인과귀속을, 일봉 ETF 하루
> 등락을 설명하는 우리 프레임워크가 재현할 수 있는가. 못 한다면 무엇이 막고 있는가.
>
> **답.** 재현 못 한다. 막는 것은 데이터 해상도가 아니라 **자료구조 6개**다. 해상도가
> 막는 것은 보고서 증거 8종 중 2종(시차·개입)뿐이고, 나머지 6종은 전부 일봉으로
> 번역된다. 우리가 못 쓰는 이유는 **쓸 칸이 없어서**다.

## 0. 출처 접근 상태

전문 확보한 것과 못 한 것을 먼저 가른다 — 아래 모든 주장의 등급이 여기서 나온다.

| 문헌 | 접근 | 비고 |
|---|---|---|
| CFTC-SEC *Findings Regarding the Market Events of May 6, 2010* (2010-09-30) | ✅ 전문 | CFTC 미러 (sec.gov 403) |
| CFTC-SEC *Preliminary Findings* (2010-05-18) | ✅ 전문 | 기각된 후보 대부분이 여기 있다 |
| Kirilenko, Kyle, Samadi, Tuzun, "The Flash Crash" (JF 2017) | ✅ 전문 | Cambridge Apollo accepted version |
| Menkveld & Yueshen (Mgmt Sci 2019) | ⚠️ 온라인 부록 전문 · 본문 19쪽 미확보 | 공개 사본 부재 확인 |
| NTSB *Writing Guide* + AAR-14/01 (Asiana 214) | ✅ 전문 | 실제 대조군 |
| Collier 2011 "Understanding Process Tracing" | ✅ 전문 | Table 1–7 |
| Ricks & Liu 2018 (PS 51(4)) + Appendix | ✅ 전문 | |
| Beach & Pedersen 2019 2nd ed. | ⚠️ pp. i–189 (ch.1–5.6) | explaining-outcome 장(ch.6–10) 절단 |
| Machamer-Darden-Craver 2000 | ✅ pp.1–18 | |
| Van Evera 1997 pp.31–32 | ❌ 단행본 | Collier 2011:825 재구성판 사용 — 원문 문구 아님 |
| Zaks 2017 (Political Analysis 25(3):344–362) | ❌ Unpaywall `oa_status: closed` | 저자 본인 CC-BY 축약본(QMMR 15(2):40–44)에서 정의 원문 확보 |
| Fairfield & Charman 2017 (PA 25(3):363–380) | ✅ 전문 | LSE 승인본 |
| Fairfield & Charman *SIBI* (2022) | ⚠️ Ch.1 전문 · Table 4.1 셀값 미확보 | |
| Halpern *Actual Causality* (2016) | ⚠️ ch.1–3 저자공개본 | ch.4–8 미공개 |
| Halpern 2015 (arXiv:1505.00162) · Halpern-Pearl 2005 · Halpern-Hitchcock 2015 | ✅ 전문 | |
| Tian & Pearl 2000 (UCLA R-271-A) | ✅ 전문 | |
| Chockler & Halpern 2004 (JAIR) | ✅ 전문 | |
| Abadie 2021 (JEL) · ADH 2010 · Abadie-Gardeazabal 2003 | ✅ 전문 | |
| Chernozhukov-Wüthrich-Zhu 2021 | ✅ 전문 | arXiv 1712.09089v7 |
| Kothari & Warner 2007 + 위임처(CLM 1997 · BW 1985) | ✅ 전문 | |
| Amihud 2002 · Corwin-Schultz(유도) · Abdi-Ranaldo | ✅ 전문/유도 전문 | |

표기: `[1차]` 본문 직독 · `[1차-저자]` 저자 본인의 다른 공개 지면 · `[2차]` 제3자 ·
`[INFERENCE]` 확보 문장에서 도출.

---

## 1. 브리핑에 대한 정정

사용자 브리핑은 방향이 옳지만 문헌 대조에서 여섯 군데가 어긋난다. 설계가 여기서 갈리므로
먼저 정정한다.

### 1.1 "NTSB Probable Cause 는 단수" — **틀렸다** `[1차]`

NTSB *Writing Guide*:

> "The probable cause **can be a series of events or a listing of separate causal
> factors.**"

실제 Asiana 214 (AAR-14/01) 의 Probable Cause 는 **4개 병렬**이다. 그리고 Findings 개수에
상한이 없다 — 규칙은 `|Findings| = |ANALYSIS 이슈 절|` 전단사이고 Asiana 는 30개다.

**Finding #1 은 음성 소견 일괄이다**: *"The following were not factors in the accident: …"*
가 맨 앞에 온다. 부정 소견이 긍정 소견보다 먼저다.

→ 우리 `Findings.probable_cause: Disposition | None` 은 **규약 위반**이다. Halpern 쪽에서도
같은 결론이 독립적으로 나온다: modified HP 의 원인은 연언 집합일 수 있다($L{=}1 \wedge MD{=}1$).

### 1.2 "Flash Crash 보고서가 NTSB 형식의 모범" — **아니다** `[1차]`

CFTC-SEC 보고서에는 **Findings 절도 Probable Cause 문장도 없다.** 목차는
`EXECUTIVE SUMMARY / I. 지수 상품 거래 / II. 참가자와 유동성 철수 / III. 추가 요인 /
IV. 호가장 분석` 이고, 인과 판정은 각 절 말미에 흩어진 문장이다("we conclude that NYSE
LRPs did not cause…"). 처분에 해당하는 것은 "LESSONS LEARNED"(= 권고)뿐이다.

두 문서는 **다른 장르**다. 보고서에서 배울 것은 *처분 형식*이 아니라 **기각의 기술**이고,
처분 형식은 NTSB 에서 따로 가져와야 한다. 우리는 둘을 섞어서 잘못 채택했다.

### 1.3 "Van Evera 4검정을 쓰면 된다" — Beach & Pedersen 이 이를 직접 비판한다 `[1차]`

B&P 2019 §5.2:

> "how can we claim that our hoop test is evidence when we are **never told what is
> jumping through the hoop?**"

대체물은 두 연속량이다: **theoretical certainty** $p(e \mid h)$ 와 **theoretical
uniqueness** $p(e \mid \neg h)$.

그리고 Collier 2011:825 자신이 명시한다 — 검정 유형은 증거의 속성이 아니라
**(관측, 가설, 배경가정) 삼중항**의 속성이다. 같은 관측이 강한 배경가정에서는 hoop(소거),
약한 가정에서는 straw(생존)다.

**실행 검증** (ProcessTracing 이 §2.3 판정함수를 돌려 B&P 2019:181 Tannenwald 계산 재현):
prior 0.3, certainty 0.7, uniqueness 0.2 → 발견 시 사후 0.6000 (책 0.6), 미발견 0.1385
(책 0.14). 그런데 **같은 증거를 HI=0.85/LO=0.15 로 이산화하면 straw 로 분류된다** —
사전확률을 두 배로 올린 증거가 straw 다.

→ **4검정은 손실 압축이다.** certainty·uniqueness 를 1급 필드로 저장하고 `test_type()` 은
감사용 유도값으로만 둔다. 저장하면 삼중항 의존성이 사라져 거짓말이 된다.

### 1.4 Collier 2011 은 자기모순이다 — rival 파급을 자동계산하지 마라 `[1차]`

출판판 Table 1 은 straw/hoop/smoking gun 통과가 rival 을 *slightly/somewhat/substantially*
약화시킨다고 적는다. 그런데 같은 저자의 2010 교육판은 세 검정 모두
**"no implications for rival hypotheses"** 라고 적는다.

→ 저자 내부 모순. 우리는 rival 파급을 **자동 계산하지 않는다**. 그리고 Zaks 가 왜인지
설명한다(§1.5).

### 1.5 "경쟁 DAG 를 병렬로 유지하라" — 옳지만 실행 형태가 다르다 `[1차-저자]`

브리핑의 진단("처음부터 하나의 DAG 를 확정하면 편향된다")은 정확하다. 그러나 처방을
"DAG A/B/C 를 병렬 유지"로 두면 비용이 폭발하고, 문헌은 더 싼 곳을 지목한다.

Zaks 2017 의 핵심 문장:

> "**testing the relative 'causal force' is only possible when two explanations can
> simultaneously but independently bring about the outcome (i.e., under relationships
> of coincidence).**"

*relative causal force* = 우리의 `share`. 즉 **`share` 는 coincident 관계에서만 정의된다.**
나머지 관계에서 share 를 계산하는 것은 정의되지 않은 양을 합산하는 것이다.

그래서 필요한 것은 N개의 DAG 가 아니라 **가설 쌍마다의 관계 유형**이다. 관계가 회계를
결정하고, 회계가 결론을 결정한다. 4유형 (2017판, 증거론적):

| 유형 | 원문 정의 | 가르는 축 |
|---|---|---|
| **mutually exclusive** | "acceptance of one entails rejection of the other" | 세계가 하나만 참 |
| **coincident** | "both could simultaneously account for the phenomenon, **with evidence in favor of one not affecting the other**" | 공동 산출 + 증거 독립 |
| **congruent** | "not only do both account for the outcome, but **evidence in favor of one theory also supports the other**" | 공동 산출 + 증거 연동 |
| **inclusive** | "**one theory represents a novel extension of the other**" | 포섭 (뉴턴역학 ⊂ 일반상대성) |

⚠️ **판본 주의.** 2011 학회본은 3유형이었고 `congruent` 가 **존재론적** 정의("they interact
and jointly produce the outcome")였다. 웹에 도는 요약 다수가 구판이다. 2017판 정의는
**증거론적**이고, 그래서 `predicts`/`denies` 대수로 번역된다 — 판본 선택이 구현을 가른다.

그리고 Zaks 본인이 한 쌍에 두 라벨을 겹쳐 쓴다("congruent **and** inclusive"). → 단일 유형
강제 분류 금지.

### 1.6 Chamberlin 인용의 절반이 빠져 있다 `[1차]`

우리 코드(`contracts.py`, `p2_hypotheses.py`)는 Chamberlin 1890 을 **"애착 분산"** 장치로만
인용한다. 그 부분은 정확하다. 그러나 Chamberlin 의 목적은 소거가 아니라 **몫의 배분**이었다:

> "…since it often proves in the end that several agencies were conjoined in the
> production of the phenomena. **Honors must often be divided between hypotheses.**
> One of the superiorities of multiple hypotheses as a working mode lies just here."
>
> "The full solution therefore involves not only the recognition of multiple
> participation but **an estimate of the measure and mode of each participation.**
> … The method of the single working hypothesis … **is incompetent.**"

**이것이 우리 `share` 회계의 1890년판 원전이다.** 그런데 Platt 1964 는 이 대목을 한 번도
언급하지 않는다 — Platt 에게 다중가설은 *배타적 소거*를 심리적으로 가능하게 하는 장치이고,
Chamberlin 에게는 *분배적 설명*에 도달하는 장치다. **두 저자를 나란히 인용하는 우리 주석은
서로 다른 목적을 하나로 묶고 있고, 그 긴장이 정확히 Zaks 가 2017 에 재발견한 문제다.**

(서지: Platt 이 인용한 것은 1890 *Science* 판이 아니라 1897 *J. Geol.* 5:837 개정·축약본이다.)

---

## 2. Flash Crash 를 우리 산출물 형식에 채워 본 결과

표현력 시험이다. 채우다 막히는 칸이 곧 결함이다.

### 2.1 보고서가 실제로 한 일 — 인과 연쇄와 정량 증거 `[1차]`

| 국면 | 사실 | 수치 |
|---|---|---|
| 배경조건 | 시장 불안·유동성 저하 | — |
| **촉발원** | 헤지 목적 E-Mini 매도 프로그램 | **75,000 계약 (~$4.1bn)**, 실행률 = **직전 1분 거래량의 9%**, price/time **무제약** |
| 이례성 대조 | 같은 주체의 직전 동급 실행 | 5시간 초과 → 5/6 은 **약 20분** |
| 전파 | E-Mini → SPY → 개별주 (차익거래) | 2:41–2:44 E-Mini **−3% (4분)** |
| **증폭 1** (피드백) | 9%-of-volume 이 거래량 증가에 반응해 매도 가속 | 알고리즘이 "not yet fully absorbed" 물량 위에 추가 투입 |
| **증폭 2** (hot potato) | HFT 간 재고 떠넘기기 | 2:45:13–27 (**14초**) **27,000 계약** 거래, 순매수 **200 계약** → **135:1** |
| 깊이 붕괴 | 매수 depth | **$58m (<1%)**, 15초에 추가 −1.7%, 저점 1056 |
| **종료** | CME Stop Logic | 2:45:28–33 **5초 정지** → depth 재충전 → 3:08 pre-drop 복귀 |
| 제2 위기 | 개별종목 파탄 | 20,000건 / 550만주 / 300+종목이 −60%+ |

### 2.2 기각의 기술 — **각 기각은 "참이면 반드시 나타났을 관측 하나"를 특정한다** `[1차]`

| 기각된 후보 | 무엇으로 죽였는가 | 증거 유형 |
|---|---|---|
| fat finger · 해킹 · 테러 | CME 가격밴드 **±12pt(≈1%)** + 최대주문 **2,000 계약** → 구조적으로 불가능 | **구조적 배제** |
| P&G 특정 종목 뉴스 | **시차** — PG 하락 개시 **2:44** vs 지수 **2:40** | 시차 |
| 기초종목 유동성 사건 | S&P500 집계 호가장이 하락 내내 **매수/매도 균형 유지** + E-Mini depth 붕괴가 SPY 에 **선행하고 먼저 회복** | 시차 + 음성대조 |
| NYSE LRP | 파탄거래 326종목의 **80%+가 비NYSE** + 갇힌 유동성 83건 중 **19건/12종목뿐** + LRP 중 평균 bid depth 증분 **133주** | 노출-결과 불일치 |
| Nasdaq self-help | Arca 점유율 **14.7%→15.4%** (증가) + 우회 물량은 Arca 체결의 **13.6%** | 노출-결과 불일치 |
| CQS 지연 · quote stuffing | 통합피드는 **별개 시장이 아니므로** 피드 간 차익 원리적 불가 | 구조적 배제 |

**패턴**: 6건 중 3건이 **통계가 아니다.** 제도·규칙·구조가 가설을 죽인다. 우리 P5 는
`sql` + `executable` 을 요구하므로 **이 세 기각을 낼 수 없다.**

### 2.3 Kirilenko 의 "원인 아님, 증폭" 판정 근거 `[1차]`

- **용량 논증**: 전체 intraday 중개자 합산 순재고가 4일 내내 **6,000 계약 미만** — 75,000
  프로그램보다 **한 자릿수 작다**. HFT 개별 상한 4,000, MM 1,500.
- **구조 불변**: down 국면 상호작용 계수 F-검정이 HFT 는 **기각 실패**, Market Maker 는
  **1% 기각**. 같은 검정, 정반대 결과.
- **증폭의 근거**: 가격상승 직전 마지막 100계약의 HFT 공격적 점유율 **34.04% → 57.70%**,
  직후 **14.84%** (quote sniping).

### 2.4 Menkveld-Yueshen 의 반박 — 우리에게 가장 값진 것 `[1차, 온라인 부록]`

반박 대상은 **기여가 아니라 충분성과 메커니즘**이다("did contribute" 는 인정).

- **지표**: 가격갭도 재고도 아닌 **E-mini/SPY 누적 로그수익률 차 $z(t)$ 의 정상성(공적분)
  붕괴**. 25ms 해상도, ADF+ML 그리드, 40 lag, 5%.
- **시각**: 붕괴 $T_1$ = 14:44:27.525 (CME 정지보다 **60.6초 앞선다**), 복구 $T_2$ = 14:53:19.425.
- **결정적 한 방**: 붕괴 구간에서 **매도자 자신의 공격강도가 $120k→$40k (−66%)** 로
  줄었고 순매도의 약 **4%** 였다. 타 참가자는 **+444%**, 가격은 10배 빨리 하락.
  → **주 가설의 처치 변수가 결과와 역상관이다.**
- **효과수정**: 동일 $1mln 충격의 E-mini 장기영향이 P1 **−0.03bps** → P2 **−2.72bps**
  (**90배**). $t=2.48$, P2 에서만 유의.
- **흐름 예산**: 하락 속도의 **43% 만 설명**, 57% 를 미설명으로 명시한다.

**→ 최고 가치 이식 대상.** $z(t)$ 공적분 검정은 25ms 를 요구하지만 **논리는 요구하지
않는다.** 일봉 ETF 의 정확한 대응물은 **NAV 대비 프리미엄/디스카운트의 정상성**이다 —
창설/환매 차익거래가 건강하면 평균회귀, 손상되면 지속. 같은 판정("채널이 끊겼다")을 같은
통계로 다른 해상도에서 낸다. 우리는 `premium_discount_contribution_return` 을 **이미
적재하고 있다.**

### 2.5 해상도 판정 — 무엇이 정말 막혀 있나

보고서 증거 8종 중 **2종만** 원천적으로 닫혀 있다.

🔴 **이식 불가** (초·분 단위 요구): 상품 간 선행성 · 장중 V자 형상 · hot potato 비율(14초) ·
호가장 궤적 · 5초 정지 개입 · 초 단위 재고-가격 회귀 · stub quote.

🟢 **이식 가능** (논리가 해상도와 무관):

| 보고서 도구 | 일봉 ETF 번역 | 우리 상태 |
|---|---|---|
| **구조적 배제** | LULD·창설/환매 차익·SSR·상하한가·VI·포지션 한도 | ⛔ **없음 — 가장 아까운 미사용 자산** |
| **용량 논증** | 순유출 vs ADV·AUM·창설/환매 용량 | ⛔ 없음 |
| **자기 처치 dose-check** | 제안된 원인의 강도를 창 안에서 분해해 결과와 단조성 확인 | ⛔ 없음 |
| **차익거래 채널 건전성** | **NAV 프리미엄/디스카운트 정상성** | ⛔ 없음 (데이터는 있다) |
| **효과수정** | 채널 OK/손상 레짐별로 같은 크기 유출의 충격 비교 | ⛔ 없음 |
| **일시성 vs 영속성** | **이후 k일 되돌림** — 유동성 사건과 정보 사건을 가르는 유일한 관측 | ⛔ 없음 (미래 관측이라 P9 개정 슬롯) |
| 음성 대조 결과 | 해당 종목 미보유 ETF 는 안 움직여야 | 🟢 P7 에 있음 |
| 혼재 스크린 | 같은 창의 타 공시 보유 기업 제외 | 🟢 P7 에 있음 |
| 예산 회계 | 그대로 | 🟢 있음 |
| 명시적 부정 소견 | 그대로 | 🟢 `not_contributing` 있음 (순서·상한 문제) |

> **결론**: 막고 있는 것은 데이터가 아니라 스키마다. 그리고 시차·개입이 닫혀 있다는 사실은
> **P4 가 명시적으로 선언해야 한다** — "이 해상도에서 시차 판별이 불가능하므로 방향성
> 주장은 `mechanism_compatible` 을 넘을 수 없다."

---

## 3. 실측된 결함 — 코드에서 확인한 것

문헌 대조와 별개로, 조사 중 **현재 코드에서 실제로 확인된** 것들이다.

### 3.1 🔴 우리는 있는 데이터를 없다고 선언하고 있다

`adapters/sql_surface.py` 의 `SCHEMA` 는 모델에게 이렇게 말한다:

```
원장에 없는 것 - 물어봐야 소용없고 …
  · 투자자 유형별 수급 (누가 샀는지 모른다)
```

**거짓이다.** `investor_flow_daily` (마이그레이션 `V202607220001`) 가 존재하고 매일 적재된다 —
투자자 **13종 × (순매수 수량·대금) = 26 컬럼**, grain 은 (금융상품, 거래일), 개인·외국인·
기관계는 `NOT NULL`, 기관 세부 10종(증권·투신·연기금·사모·은행·보험·종금·기타법인·
기타단체·기타)까지 있다. SFN 파이프라인 `CollectKisInvestor → NormalizeInvestor →
LoadEtfFlow` 가 끝단이다.

미국 문헌이 **분기 13F 로 재구성**하는 Coval-Stafford 류 flow pressure 를 **일별 실측으로
보유하면서 미사용**이다.

**이것이 "메커니즘 영역 편향"의 기계적 원인이다.** 브리핑은 "뉴스만 검색하면 정보·기대
영역으로 편향된다"고 지적했는데, 우리 경우 편향의 원인은 P2 의 상상력이 아니라 **표면이
포지션·수급 영역을 닫아 놨다는 사실**이다. 모델은 열려 있지 않은 영역에 가설을 세울 수 없다.

같은 종류로 `etf_contribution_observation.fx_contribution_return`(교차자산 일부)도 계산돼
있으나 표면에 없다.

### 3.2 진짜로 없는 것 (확인함)

`price_daily` 는 `close_price` · `adjusted_close_price` · `volume` · `turnover_value` 뿐 —
**OHLC 의 H/L 이 없다.** 따라서:

- Corwin-Schultz (2012) high-low spread estimator: **계산 불가** (H/L 필수)
- Abdi-Ranaldo (2017): **계산 불가** (H/L 필수)
- 정적 VI 판정 (`H ≥ 1.10 × C_prev`): **계산 불가**
- **Amihud (2002) illiquidity: 계산 가능** — $\text{ILLIQ} = \frac{1}{D}\sum_d |R_d| / \text{VOLD}_d$,
  `turnover_value` 가 곧 $\text{VOLD}$ 다.

⚠️ 그리고 MicroDomains 가 시뮬레이션으로 확인한 함정: Corwin-Schultz 를 쌍별 계산 후 평균
(음수→0)하면 **참 스프레드 0 에서 1.10% 를 보고하는 거짓양성 기계**가 된다. $\beta,\gamma$ 를
창 평균한 뒤 $\alpha$ 를 1회 계산해야 편향이 붕괴한다(0.0013). 나중에 H/L 을 얻더라도 이
순서를 지켜야 한다.

### 3.3 한국시장 제도 — 구조적 배제에 쓸 수 있는 것 `[1차, KRX 원문]`

| 제도 | 일봉 관측 가능성 | 판별력 |
|---|---|---|
| **상하한가 ±30%** (2015-06-15~, ETF 포함) | `\|r\| ≥ 0.299` 로 **종가만으로 확정 식별** | 강함 + **관측 절단** — 예산이 하한이 된다 |
| **공매도 규제 4구간** | 하드코딩 4줄, 수집비용 0 | **2021-05-03 ~ 2023-11-05 는 지수편입 여부가 공매도 가능성을 가르는 교과서적 DiD 자연실험** — KR 데이터에서 P4 가 `identified` 를 줄 수 있는 사실상 유일한 구조 |
| 정적 VI ±10% | H/L 없어 불가 | — |
| 동적 VI (3%/6%) | 직전 체결가 기준 — 일봉·5분봉 모두 복원 불가 | 구조적 부재 선언 대상 |
| 사이드카 | 시장 전체라 P0 기준선에 흡수됨 | 종목별 흔적 없음 |
| 서킷브레이커 | 종가 기준은 필요조건도 아님 | 약함 |

공매도 규제 구간(2020-03-16 전면금지 / 2021-05-03 KOSPI200·KOSDAQ150 350종목만 재개 /
2023-11-06 전면금지 / 2025-03-31 전면재개)은 **지금 당장 상수 4줄로 넣을 수 있다.**

### 3.4 반사실 — 합성통제는 기각한다 `[1차 + 실측]`

Counterfactual 조사가 세 후보를 실측 비교했다.

| 후보 | 판정 | 근거 |
|---|---|---|
| (i) 현행 횡단면 평균 $r - \bar r$ | **보정 후 유지** | 베타 계수 하나 추가로 잔차 sd **1.856% → 1.671%** (oracle 1.652%) |
| (ii) 합성통제 (Abadie) | **기각** | 실측 SCM 1.789% > demean+β 1.671%. **과적합** |
| (iii) 구성종목 분해 | **채택 — explanandum 을 여기로 옮긴다** | 반사실이 아니라 항등식. **n=1 을 n=N 으로 바꾼다** |

SCM 기각 근거는 6개이고 각각 독립 충분하다: (1) Abadie 2021 p.412 의 차분 경고 정면 해당
— 일별 수익률 = 로그가격 1차차분, 특이충격 시계열 독립 = 그가 명시한 최악의 경우
(2) pp.408–409 변동성 조건 불충족, $R^2 \approx 30\%$ (3) pp.410–411 No Interference —
donor 오염이 구조적 (4) KW p.15: 단기에서 정상수익률 모형은 "not highly sensitive"
(5) 실측 과적합 (6) **AG2003 §III — SCM 원저자가 일별 주가 단일사건에 SCM 을 쓰지 않는다.**

**결정적 수치 — 잡음 바닥이 지배한다.** 반사실 선택이 잔차 sd 를 움직이는 폭은 최대
**0.20%p**, 제거 불가 특이잡음은 **1.65%p**. 반사실 선택은 2차 효과다.

그리고 검정력:

$$\sigma = 1.68\%/\text{일} \Rightarrow \text{80\% 검정력}@5\% \text{에 필요한 효과} = \mathbf{4.71\%/일}$$

$$\mathrm{sd}(u_{\text{ETF}}) = \sigma\sqrt{\tfrac{1}{N_{\text{eff}}} + \rho\left(1 - \tfrac{1}{N_{\text{eff}}}\right)}$$

$\rho = 0.25$ 면 **종목 수와 무관하게 검출 하한이 2.4%/일에서 정체**한다. 섹터 ETF 는 정의상
$\rho$ 를 극대화한다.

> **이것이 이 조사에서 가장 실무적으로 무거운 발견이다.** 우리가 설명하려는 잔차의
> 대부분은 **원리적으로 검출 불가능한 크기**다. `|residual| < MDE₈₀` 인 셀에서 "유의하지
> 않다"는 정보가 아니고, 어떤 서사도 반증 불가능하다. **P6 이전에 검정력 축이 와야 한다.**

부수 정정: Abadie 2021 에 "treated unit must lie in the convex hull" 은 없다 — 원문은
"fall **close to** the convex hull" (p.411). 사전기간 길이·donor pool 크기 권장 수치도
하나도 없다(전부 정성적). ADH2010 최종 p-value 는 1/38 이 아니라 **1/39 = 0.026**.

### 3.5 Fairfield & Charman 이 우리 구조를 직접 지목한다 `[1차]`

책 Ch.8 의 "frequentist 잔재" 4종 중 (iv):

> "**associating each potentially contributing independent variable with a separate
> hypothesis**, rather than articulating a comprehensive hypothesis that explains how
> all variables deemed salient bring about the outcome."

진단: "may arise from a **frequentist regression perspective** that focusses on the causal
contribution of each independent variable in turn."

**우리 P2 가 정확히 이것을 한다** — 세션마다 `cause_label` 을 하나씩 다르게 강제하고
(중복이면 거부), P8 이 각각에 share 를 매긴다.

그리고 결정적 구분 (SIBI 87):

> "**mutual exclusivity of hypotheses is conceptually distinct from exclusivity of their
> constituent independent variables, causal factors, or mechanisms** … they can also
> include many or all of the same independent variables and be mutually exclusive, so
> long as they posit **different functional relationships** among the variables."

→ 같은 인과 요인을 공유해도 함수형이 다르면 배타적이다. 쿠키단지 예시가 정답 틀이다:
H1 = 아이 단독, H2 = 개 단독, H3 = 공모, H4 = 각자 독립, H5 = 처음엔 독립 나중에 협력.
**"둘 다 기여"는 관계가 아니라 별도의 제3 가설이다.**

⚠️ 여기서 문헌이 두 진영으로 갈린다:

| 진영 | 처방 | 대표 |
|---|---|---|
| 관계를 인정하고 모델링 | 비배타 경쟁을 정도로 측정 | Zaks 2017 · Schupbach-Glass 2017 |
| 배타성을 복원 | 가설 공간을 재분할 | Fairfield-Charman |

**우리는 Zaks 를 채택한다.** 이유: 재분할은 가설 수가 조합폭발하고(요인 $k$ 개 → 함수형
분할 $\gg 2^k$), 우리는 LLM 세션 예산이 유한하며, 무엇보다 **예산 회계가 이미 분배적**이기
때문이다(Chamberlin 계보). Henderson 이 이 선택에 형식적 근거를 준다 — DAG(구조)끼리는
배타적으로 경쟁시키고 **share 는 그 구조 안의 파라미터로 추정**한다.

---

## 4. 설계 — 무엇을 바꾸는가

### C1. `role` — 역할 축, 지문이 감사한다

Halpern-Hitchcock 이 **배경조건 vs 촉발원**의 형식적 기반을 준다 `[1차]`:

> 배경조건 ≡ 실제값이 그 참조류에서 **default** · 촉발원 ≡ **deviant**

그리고 이것은 **내재적 속성이 아니라 참조류 상대적**이다 — 진공챔버에서는 산소가 원인이
된다(HH §7.3 명시). 판정 규칙은 AC2⁺(a):

> witness world $s_{X \leftarrow x', W \leftarrow w}$ $\succeq$ 실제 world $s_u$.
> "witness 가 실제보다 **덜 정상적이면 안 된다**." (Kahneman-Miller 1986 의 형식화)

**우리 P1 은 이미 이 측정을 하고 있다.** `type_extremity.band` 는 문자 그대로 참조류 상대적
전형성이고, `pre_drift` 는 HH §5 의 "start time 을 default state 로"의 구현이다. 그런데
산출이 LLM 프롬프트용 산문(`kills`)뿐이라 **판정장치가 아니라 힌트로 소비된다.**

→ `deviance(axis) → default | mild | deviant | extreme | unknown` 를 P1 에서 유도하고,
P2 가 신고한 `role` 을 **감사**한다. `background` 인데 처치 축이 `deviant` 면 위반이고,
`trigger` 인데 `default` 면 위반이다.

**불변식 (전부 문헌에서 강제됨):**

1. `available=False → "unknown"`, 절대 `"default"` 가 아니다. 못 쟀다는 것과 전형적이라는
   것은 다르다. `default` 로 접으면 **측정 실패가 자동으로 배경조건 판정을 만들어낸다.**
2. 순서는 **도출돼야 하고 선언돼서는 안 된다.** Halpern-Hitchcock 2010 의 경고:
   "the modeler can now render any claim … false, simply by choosing a normality order."
   **P2 의 LLM 이 정규성을 주장하게 하면 안 된다** — 그 순간 원하는 판정에 도달하는 손잡이다.
3. **전순서를 만들지 마라.** 비교불가를 살려야 bogus prevention 류가 풀린다(HH §7.4).

### C2. `Relation` — Zaks 관계 유형 + 유형별 예산 회계

```
mutually_exclusive · coincident · congruent · inclusive · causal · unknown
```

`causal`(H1→H2→Y)은 **RAR 에 없다** — 정직하게 밝힌다. RAR 타이폴로지는 증거론적 관계이지
인과 순서가 아니고, `inclusive` 는 포섭이지 전파가 아니다. 다섯 번째 관계가 필요하고
회계는 매개분석에서 빌린다.

| 관계 | 예산 연산 | 틀리면 |
|---|---|---|
| **coincident** | $s_1 + s_2$ — **합산. 지금 코드가 유일하게 맞는 경우** | — |
| **mutually exclusive** | **합산 금지.** 같은 슬롯을 두고 경쟁 → $\max$ | **거짓 초과.** 배타적 둘을 더해 `over_budget` → 둘 다 `undetermined`. **정상 그래프를 산술이 죽인다** |
| **congruent** | 포함-배제. 교집합 못 재면 밴드 $[\max(s_1,s_2),\ s_1+s_2]$ | **중복 계상** |
| **inclusive** | **한 줄로 접는다** — 두 항이 아니다 | **범주오류.** special case 를 독립 경쟁자로 |
| **causal** | $s(H_1) := \mathrm{NDE}(H_1)$, $s(H_2) := \mathrm{NIE}(H_1) + s_{\text{own}}(H_2)$ | **매개분 이중계상.** 확정적 초과 |
| **unknown** | **share 주장 차단** | 조용한 합산 |

미판정 기본값은 `coincident` 가 **아니라** `unknown` 이다. 기본값을 coincident 로 두면
share 합산이 조용히 일어난다.

경로 분해가 옳은 이유: 예산이 정확히 한 번 닫히고, $s_k$ 가 여전히 가법적이며(수익률
가법성에 기대는 1차 예산 항등식 유지), NTSB 형식과 정합한다(사슬의 뿌리가 probable_cause,
매개자가 contributing).

**판정의 기계화** — 2017판 정의가 증거론적이므로 `predicts`/`denies` 대수로 번역된다.
단 SIBI 89 가 순서를 강제한다:

> "alternative explanations may make observationally equivalent predictions on many
> pieces of evidence—**they need only make different predictions in at least one actual
> or possible instance**"

→ **겹침이 아니라 갈림을 먼저 본다.** 갈림 하나가 겹침 전부를 이긴다.

⚠️ 알려진 한계: `predicts`/`denies` 는 **적어둔** 예측이지 **가능한** 예측 전부가 아니다.
이 함수는 **배타성을 과소 판정하는 쪽으로 편향**돼 있다. 안전한 방향(거짓 배제보다 거짓
병합이 낫다)이지만 편향 방향을 알고 써야 한다.

### C3. WOE (decibel) — `common_prediction: bool` 을 대체

$$\mathrm{WOE}(H_i : H_j) = 10\log_{10}\frac{P(E \mid H_i I)}{P(E \mid H_j I)} \quad \text{(Good 1985; F\&C 2017 eq.4)}$$

로그인 이유 `[1차]`: "Use of a **linear scale fosters arbitrary quantification and
precludes effective use of the full dynamic range** of probabilities" + Weber-Fechner.
그리고 로그의 실효는 **가법성** — 증거를 더하고, 3자 비교를 쌍별 두 개의 합으로 얻는다.

**해상도 하한 1 dB** (Good: "as fine-grained as we can reliably quantify"). 앵커:

| dB | 해석 |
|---|---|
| 1 | 신뢰 가능한 최소 눈금 |
| **3** | **JND** — 지각 가능한 최소 차이 |
| 5 | clearly noticeable (75%→90% ≈ 5 dB) |
| 10 | twice as loud, LR 10배 |
| 25 | strongly favors |
| **30** | **decisive** — "the data are talking clearly" |

교정 사례 `[1차]`: Bennett(2015)이 smoking gun 이라 부른 $P(E|H)=0.2$, $P(E|\neg H)=0.05$ 는
**6 dB 에 불과** — "not decisive enough by Good's standards."

→ `common_prediction ⟺ |woe_db| < 3` (JND 미만이면 아무것도 가르지 못한다). 그리고 `hj` 는
**필수**다 — F&C 최우선 경고가 $\neg H$ 상대 평가다("~H generally will not be a well-defined
proposition—H could fail to hold in an essentially infinite number of ways").

**금지**: 소수점 dB(거짓 정밀) · `db=±inf`(반증 불가) · congruent 쌍의 독립 합산.

### C4. 메커니즘 영역 8종 커버리지 원장

P2 의 어휘는 계속 열어 둔다 — 골격을 주면 모델이 노드를 만들지 않고 칸을 채운다. 대신
**닫는 것은 커버리지 보고**다: 영역마다 `opened` / `unavailable(왜)` / `not_considered`.
`not_considered` 가 곧 침묵이고, P8 이 그것을 적는다.

그리고 §3.1 의 거짓말을 고친다 — **수급 영역은 열려 있다.**

### C5. NTSB 규약 정합

`probable_cause` 단수 → **복수**. 음성 소견을 **맨 앞**에. Finding 단위 양상 어휘
(`was / likely / would likely have / may have / not a factor`).

### C6. 검정력 축 — E-value 보다 먼저

$\mathrm{MDE}_{80}$ 을 계산하고, $|residual| < \mathrm{MDE}_{80}$ 이면 **주장 상한을
강등**한다. 검정력이 없으면 교란 민감도를 논할 대상이 없다.

그리고 **무사건 가설**(브리핑 영역 8)을 1급으로: 잔차가 그 셀 자신의 귀무분포 안이면
`no_explanandum` 이고 **P2 를 호출하지 않는다.** 3겹 형식화(MicroDomains):

1. 자기 귀무분포 — EWMA 표준화 후 **경험분위** 양측 p. 정규분포 금지(fat tails).
2. 스캔 보정 — $p_{\text{scan}} = 1 - (1-p)^{n_{\text{etf}} \cdot n_{\text{days}}}$.
   200 ETF 를 매일 훑으면 $p=0.005$ 는 **매일 하나씩 나온다.**
3. 게이트 조건부 기저율 — `price_movement_trigger` 통과 셀만 분석하므로 무보정 극단성
   판정은 **순환논증**이다.

### C7. 판별자 유형 확장 — 통계 밖의 기각을 허용

| 유형 | 무엇 | 근거 |
|---|---|---|
| `pair` | 두 가설이 다르게 예측 | 기존 |
| `latent` | 원인 가설 vs 교란 U | 기존 |
| **`structural`** | **제도·규칙이 가설을 불가능하게 만든다. SQL 불필요** | fat finger 기각(±12pt), 통합피드 기각 |
| **`capacity`** | **메커니즘의 물리적 수용력 < 요구 규모** | Kirilenko 6,000 vs 75,000 |
| **`dose`** | **주 가설 자신의 처치가 결과와 단조인가** | M-Y 의 결정적 한 방 (−66%) |

`dose` 가 특히 중요하다 — 지금 P5 는 가설 *쌍*만 본다. 자기 가설의 처치-결과 단조성을
검정하지 않으면 Menkveld-Yueshen 류 반박을 **구조적으로 낼 수 없다.**

---

## 5. 구현 상태 (2026-07-31 실측)

`363 passed, 10 skipped` — 스킵 10건은 실 Postgres(`E2E_PGHOST`) 필요 케이스.

| # | 항목 | 상태 | 어디 |
|---|---|---|---|
| C1 | `role` 5종 + `deviance()` 유도 + 지문 감사 | ✅ | `contracts.deviance` · `WorldGraph.role_violations` · `p2` 프롬프트·파서 |
| C2 | `Relation` 6종 + 유형별 예산 사면 | ✅ | `contracts.Relation`·`classify` · `p3.derive_relations` · `p8._budget_forgiven` |
| C3 | WOE 데시벨 (`common_prediction` 은 이제 3 dB JND 유도값) | ✅ | `contracts.Discriminator` · `p5._woe` |
| C4 | 영역 8종 커버리지 원장 + **수급 표면 개방** | ✅ | `p8._coverage` · `sql_surface` 의 `v_flow`·`v_liquidity` |
| C5 | NTSB 정합 (PC 복수 · 양상 어휘 · 음성 소견) | ✅ | `Findings.probable_cause: list` · `Modality` · `p8._modality` |
| C6 | 검정력 축 + `no_explanandum` | ✅ | `p0._power` · `Question.underpowered`·`no_explanandum` · `run.explain` |
| C7 | `structural`·`capacity`·`dose` 판별자 | ✅ | `contracts.Discriminator.kind` · `p5._accept` · `plan.dose_failures` |

### 실측 — Flash Crash 서사를 실제로 통과시킨 결과

보고서의 인과 패키지를 가설 5개로 넣고 P3→P8 을 돌렸다. **역할이 칸을 가른다:**

```text
[PC] 촉발원    75,000 계약 헤지 매도 프로그램
[  ] 전달      차익거래를 통한 선물→현물 전달
[  ] 증폭      HFT 재고 떠넘기기 (hot potato)     <- Kirilenko "원인 아님, 증폭"
[  ] 배경조건  시장 불안·유동성 저하
[  ] 종료      CME Stop Logic 5초 정지
```

그리고 Menkveld-Yueshen 의 반박을 `dose` 판별자로 넣자(매도자 공격강도가 붕괴 구간에서
−66%, `woe_db=-14`) 촉발원이 **자기 증거로** 죽었다:

```text
not_contributing: 75,000 계약 헤지 매도 프로그램 [not_a_factor]
  자기 처치가 결과와 역방향이다 (-14 dB). 제안된 원인이 강한 자리에서 결과가 오히려 작다
```

이 기각은 **쌍 판별로는 구조적으로 나올 수 없다** — 이전 구조에는 담을 자리가 없었다.

### 구현 중 잡은 결함 5건

전부 실제로 재현해서 확인했다.

| # | 무엇 | 영향 |
|---|---|---|
| 1 | `sql_surface.SCHEMA` 가 "투자자 유형별 수급 없음"을 선언 | **거짓.** `investor_flow_daily` 26컬럼이 매일 적재된다. 수급 영역이 구조적으로 안 열리던 기계적 원인 |
| 2 | 내가 넣은 `p_scan` 을 `no_explanandum` 게이트로 사용 | **파이프라인 전체 침묵.** 250일 이력의 p 하한 1/251 + Šidák 200셀 → 모든 셀이 0.5 초과. 실측: +9% 인 날조차 차단됐다(3/3). 다중검정 보정은 "이례적이다"를 어렵게 하는 장치이지 "평범하다"를 쉽게 하는 장치가 아니다 — 방향을 뒤집어 놨었다 |
| 3 | `scan_unresolved` 가 분해능 바닥에서도 발화 | `confirmed` 가 구조적으로 도달 불가. 증거가 세다는 이유로 벌하는 상태. `at_resolution_floor` 로 분리 |
| 4 | `p5.design` 루프가 `('latent', uid)` 키만 조회 | `structural` 로 U 를 처분해도 루프가 못 봐 `done` 을 상한까지 거부(왕복 9회 낭비) + P9 대장에 "12턴 안에 아무것도 안 나왔다"는 **거짓 행** |
| 5 | `Findings.by_role` 이 합성 처분까지 셈 | `role` 기본값이 `trigger` 라 **"미설명분"이 촉발원으로 보고**됐다 |

(1·4·5 는 서브에이전트가 픽스처·변이 검사에서 발견했고, 2·3 은 내가 넣은 것을 e2e 담당이
잡았다.)

### 구현하지 않은 것 — 근거와 함께

| 항목 | 왜 안 했나 |
|---|---|
| **베타 조정 반사실** | 잔차 sd 1.856%→1.671% (oracle 1.652%). SCM 전체보다 큰 개선이지만 `cd.ar` 표면 변경이라 파급이 넓다. 식은 확정: $r_\perp = r - \hat\alpha - \hat\beta \bar r$, 추정창 $[t-250,\ t-21]$ |
| **k일 되돌림** | 미래 관측이라 `as_of` 에 원리적으로 없다. **P9 개정 슬롯**이 옳은 자리 — $t+k$ 에 원장이 스스로 개정한다. 유동성 사건과 정보 사건을 가르는 유일한 관측이라 우선순위가 높다 |
| **NAV 프리미엄 정상성** | Menkveld-Yueshen $z(t)$ 공적분의 일봉 대응물이자 최고 가치 이식 대상. `premium_discount_contribution_return` 은 있으나 시계열 표면이 없다 |
| **횡단면 스캔 풀** | 위 결함 3의 진짜 해법. 자기 이력(250일) 대신 전 ETF-일 풀(수만 셀)이면 보정 후에도 분해된다 |
| **구조방정식 $\mathcal F$** | HP AC1/AC2/AC3 를 **계산하려면** 필수. `edges` 는 인접관계뿐이라 $[\vec X \leftarrow \vec x']$ 평가 불가. 우리는 role 판정에만 normality 를 쓰고 **완전한 HP 판정은 하지 않는다** |
| **PN/PS 경계** | Tian-Pearl PN 은 효과 경계와 다른 객체다. 현재 `Identification.bounds` 는 효과 Manski 경계이고 PN 이 아니다. 질의 하나(미노출 셀의 결과율) 추가로 하한 계산 가능 |
| **공매도 규제 DiD** | 상수 4줄. 2021-05-03~2023-11-05 는 지수편입 여부가 공매도 가능성을 가르는 자연실험 — KR 에서 `identified` 를 줄 수 있는 사실상 유일한 구조. **다음 순위 1번** |
| **Amihud 축을 P1 에** | `v_liquidity` 로 조회는 가능해졌으나 지문 축으로는 안 올렸다. **다음 순위 2번** |

### 미해결 (문헌 수준)

1. Zaks 2017 p.351 의 **판정 절차 원문 미확보** — 어떤 2차 문헌도 재현하지 않는다. 우리
   `classify()` 는 대체물이 아니라 우리 데이터로 만든 **대용물**이고, 적어둔 예측만 보므로
   **배타성을 과소 판정하는 쪽으로 편향**돼 있다.
2. Menkveld-Yueshen 본문 19쪽 미확보 (온라인 부록은 전문).
3. F&C 책 Table 4.1 (정성↔dB 공식 대응표) 셀값 미확보 — 본문 실사용값으로 재구성.
4. CFTC-SEC 보고서 자체의 **내부 불일치**: Nasdaq self-help 시각이 세 군데에서 다르다
   (§II 각주32 "~2:37" / §III.2 "2:36:59" / §III.2.b "2:35:59"). 모범 사례 문서조차
   타임스탬프가 어긋난다 → P9 에 `source_inconsistency` 슬롯이 필요하다는 근거.