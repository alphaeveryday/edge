"""태깅용 LLM 호출 어댑터 — OpenAI 호환 chat/completions (ALPHA-138).

`extract.extract_assertions` 는 complete_fn 을 주입받아 벤더를 모른다. 이 모듈이 그 주입물을
만든다 — 경계를 여기 하나로 몰아, 벤더를 바꿔도 추출·검증 로직은 안 건드린다.

**OpenAI 호환 규약만 쓴다.** DeepSeek·OpenAI 를 포함한 주요 벤더가 같은 `POST
{base_url}/chat/completions` 형태를 제공하므로, 벤더별 SDK 를 붙이는 대신 base_url·model 을
env 로 바꾼다(신규 의존성 0 — stdlib urllib). analysis-engine 의 `analyze_daily.py` 가 이미
쓰는 `LLM_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL` 관례를 그대로 따른다(Rule 11).

  DATA_PIPELINE_LLM__BASE_URL=https://api.deepseek.com/v1
  DATA_PIPELINE_LLM__MODEL=deepseek-chat
  DATA_PIPELINE_LLM__API_KEY=...      # 커밋 금지 — env·시크릿으로만 주입

호출 실패는 여기서 삼키지 않고 예외로 올린다 — 기사 단위 격리는 extract 가 하고(status=
llm_error), 조용한 폴백은 태깅 커버리지 저하를 숨긴다(Rule 12).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

# OpenAI 호환 기본값 — DeepSeek. 벤더 교체는 env 로만 한다(코드 수정 불필요).
DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"
# 추출은 창작이 아니다 — 같은 기사에 같은 라벨이 나와야 재현·집계가 된다.
DEFAULT_TEMPERATURE = 0.0


def _env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(f"DATA_PIPELINE_LLM__{name}") or default


def openai_compatible_complete_fn(
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    timeout: float = 60.0,
):
    """(system, user) → 응답 문자열 callable. 키가 없으면 즉시 예외(조용한 무동작 금지).

    `response_format=json_object` 를 요청한다 — 추출 계약이 JSON 이라 벤더가 지원하면 형식
    위반을 벤더 쪽에서 막는 게 싸다. 지원 안 하는 벤더면 무시되고, 그땐 extract 의
    llm_unparseable 게이트가 잡는다(이중 방어).
    """
    key = api_key or _env("API_KEY")
    if not key:
        raise RuntimeError("DATA_PIPELINE_LLM__API_KEY 미설정 — 태깅 LLM 호출 불가")
    url = f"{(base_url or _env('BASE_URL', DEFAULT_BASE_URL)).rstrip('/')}/chat/completions"
    model_name = model or _env("MODEL", DEFAULT_MODEL)

    def complete(system: str, user: str) -> str:
        body = json.dumps({
            "model": model_name,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": DEFAULT_TEMPERATURE,
            "response_format": {"type": "json_object"},
        }).encode("utf-8")
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
