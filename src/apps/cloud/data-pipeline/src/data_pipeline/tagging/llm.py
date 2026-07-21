"""태깅용 LLM 호출 어댑터 — OpenAI 호환 chat/completions (ALPHA-138).

`extract.extract_assertions` 는 complete_fn 을 주입받아 벤더를 모른다. 이 모듈이 그 주입물을
만든다 — 경계를 여기 하나로 몰아, 벤더를 바꿔도 추출·검증 로직은 안 건드린다.

**OpenAI 호환 규약만 쓴다.** DeepSeek·OpenAI 를 포함한 주요 벤더가 같은 `POST
{base_url}/chat/completions` 형태를 제공하므로, 벤더별 SDK 를 붙이는 대신 base_url·model 만
바꾼다(신규 의존성 0 — stdlib urllib).

**설정은 인자로만 받는다** — 이 모듈은 env 를 안 읽는다. `DATA_PIPELINE_*` 는 수집 설정
(`load_settings()`)의 네임스페이스인데 LLM 은 수집 소스가 아니라 거기 안 들어간다. 그렇다고
같은 접두어로 env 를 직접 읽으면 설정 계약인 척하는 가짜 키가 된다 — 호출부가 자기 방식으로
값을 구해 넘기고, 여기선 순수 어댑터로 남는다(테스트도 env 없이 돈다).

호출 실패는 여기서 삼키지 않고 예외로 올린다 — 기사 단위 격리는 extract 가 하고(status=
llm_error), 조용한 폴백은 태깅 커버리지 저하를 숨긴다(Rule 12).
"""

from __future__ import annotations

import json
import urllib.request

# OpenAI 호환 기본값 — DeepSeek. 벤더 교체는 인자로 한다(코드 수정 불필요).
# deepseek-chat 은 2026-07-24 폐기 → v4-pro. v4 는 thinking 기본 ON 이라 complete 가
# thinking:disabled 를 명시해 순수 JSON 응답을 받는다(ALPHA-469).
DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-v4-pro"
# 추출은 창작이 아니다 — 같은 기사에 같은 라벨이 나와야 재현·집계가 된다.
DEFAULT_TEMPERATURE = 0.0


def openai_compatible_complete_fn(
    *,
    api_key: str,
    base_url: str = DEFAULT_BASE_URL,
    model: str = DEFAULT_MODEL,
    timeout: float = 60.0,
):
    """(system, user) → 응답 문자열 callable. 키가 비면 즉시 예외(조용한 무동작 금지).

    `response_format=json_object` 를 요청한다 — 추출 계약이 JSON 이라 벤더가 지원하면 형식
    위반을 벤더 쪽에서 막는 게 싸다. 지원 안 하는 벤더면 무시되고, 그땐 extract 의
    llm_unparseable 게이트가 잡는다(이중 방어).
    """
    key = api_key
    if not key:
        raise RuntimeError("태깅 LLM api_key 가 비었다 — 호출 불가")
    url = f"{base_url.rstrip('/')}/chat/completions"
    model_name = model
    # thinking:disabled 는 DeepSeek v4 전용 파라미터다 — v4 는 thinking 기본 ON 이고 켜지면
    # 구조화 JSON 이 깨진다(vllm#41132). 다른 OpenAI 호환 벤더(openai 등)엔 알 수 없는 키라
    # 400 이 난다. 벤더 중립 계약(base_url·model 만 바꿔 스왑, 코드 수정 0)을 지키려
    # 엔드포인트가 DeepSeek 일 때만 넣는다.
    is_deepseek = "deepseek" in base_url.lower()

    def complete(system: str, user: str) -> str:
        request_body: dict = {
            "model": model_name,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": DEFAULT_TEMPERATURE,
            "response_format": {"type": "json_object"},
        }
        if is_deepseek:
            request_body["thinking"] = {"type": "disabled"}
        body = json.dumps(request_body).encode("utf-8")
        request = urllib.request.Request(url, data=body, headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        })
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        try:
            return payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            # 200 인데 응답 형태가 규약 밖 — 조용한 빈 문자열 금지(그러면 파싱 실패로 둔갑해
            # 원인이 '모델이 JSON 을 못 냈다'로 오독된다).
            raise RuntimeError(f"LLM 응답 형태 이상: {payload!r:.200}") from exc

    return complete
