"""레이크 읽기·렌더 검사·실행 흐름. 모델 세션은 가짜로 바꿔 코드가 맡은 부분만 검증한다."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import shutil

import pytest

from conftest import flow_seed, WEB, needs_chromium, seed
from edge_truth_agent import news, runner, tools
from edge_truth_agent.config import Config
from edge_truth_agent.session import SessionResult
from edge_truth_agent.store import LocalStore

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 21, 10, 5, tzinfo=KST)


def bk(title: str, content: str, link: str, news_id: str = "01100101.20261021093000001") -> dict:
    return {"TITLE": title, "CONTENT": content, "PROVIDER": "연합뉴스", "PROVIDER_LINK_PAGE": link, "NEWS_ID": news_id,
            "DATE": "20261021"}


class FakeLake:
    """1분 원본 page 와 정제본을 흉내 낸다."""

    def __init__(self, pages: dict[str, list[dict]]):
        self.pages, self.reads = pages, []

    def minute_keys(self, session_date):
        return [k for k in self.pages if f"session_date={session_date}/" in k]

    def read_minute(self, key):
        self.reads.append(key)
        return [a for a in map(news.from_bigkinds, self.pages[key]) if a]

    def canonical(self, published_date, known_etag):
        return [], None


def test_from_bigkinds_reuses_pipeline_identity_and_drops_untitled_rows():
    # 기사 id 는 data-pipeline 정체성 규약(원문 URL 해시)을 그대로 써야 레인 간 같은 기사로 묶인다.
    from data_pipeline.parse import news_article_id
    row = bk("하이브 기소", "요약", "https://www.yna.co.kr/view/AKR1")
    assert news.from_bigkinds(row).id == news_article_id(row)
    assert news.from_bigkinds(bk("", "요약", "https://x")) is None


def test_collect_reads_each_minute_page_once():
    # 같은 page 를 매시 다시 읽으면 하루 수천 건 GET 이 반복된다.
    key = news.minute_prefix("2026-10-21") + "window=0930/attempt=1/page=1.ndjson"
    lake = FakeLake({key: [bk("하이브 기소", "요약", "https://www.yna.co.kr/view/AKR1")]})
    state: dict = {}
    assert len(news.collect(lake, state, date(2026, 10, 21), 21)) == 1
    assert news.collect(lake, state, date(2026, 10, 21), 21) == []
    assert lake.reads == [key]


@needs_chromium
def test_render_catches_a_broken_page_script(tmp_path):
    # 계약은 통과해도 화면 코드가 깨지면 게시하면 안 된다 — 렌더 검사가 그것을 잡아야 한다.
    from edge_truth_agent.render import WEB_FILES, render
    web = tmp_path / "web"
    web.mkdir()
    for name in WEB_FILES:
        shutil.copy(WEB / name, web / name)
    cases = [seed("hybe-ipo-2019")]
    assert render(cases, web, ["hybe-ipo-2019"])["hybe-ipo-2019"]["errors"] == []
    (web / "common.js").write_text((web / "common.js").read_text(encoding="utf-8") + "\nthrow new Error('boom');", encoding="utf-8")
    result = render(cases, web, ["hybe-ipo-2019"], timeout_ms=5000)
    assert result["index"]["errors"] and result["hybe-ipo-2019"]["errors"]


def _cfg(tmp_path: Path) -> Config:
    return Config(store=str(tmp_path / "store"), publish=str(tmp_path / "public"), lake_bucket=None, deepseek_key="test",
                  exa_key=None, model="deepseek/deepseek-flash", thinking="low", web_dir=WEB, pi_dir=tmp_path,
                  python="python", max_sessions=4, session_timeout=60, max_tool_calls=30, corpus_days=21)


def _lake_with_indictment() -> FakeLake:
    key = news.minute_prefix("2026-10-21") + "window=0930/attempt=1/page=1.ndjson"
    return FakeLake({key: [bk("방시혁 하이브 의장 검찰 기소", "서울남부지검이 방시혁 의장을 자본시장법 위반 혐의로 기소했다.",
                              "https://www.yna.co.kr/view/AKR20261021000100001")]})


def _planned(fake):
    """계획 세션 역할: 새 기사가 있는 후보를 순서대로 고르고 나머지는 건너뛴다. 사건 세션은 fake 에 맡긴다."""
    def run(cfg, ws, *, case_id, mode, **kw):
        if mode != "plan":
            return fake(cfg, ws, case_id=case_id, mode=mode, **kw)
        rows = ws.index()
        tools.set_plan(ws, {"order": [{"case": r["id"], "reason": "새 기사"} for r in rows if r["newArticles"]],
                            "skip": [r["id"] for r in rows if not r["newArticles"]]})
        state = ws.state
        state["done"] = True
        ws.save_state(state)
        return SessionResult(case_id=case_id, done=True)
    return run


def _editing_session(text: str):
    """세션 대신 도구를 직접 불러 사건을 고치고 끝내는 가짜 세션."""
    def fake(cfg, ws, *, case_id, mode, prompt, corpus_path, cases_dir):
        tools.view_case(ws, {})
        tools.set_value(ws, {"path": "legalStatus", "value": text})
        state = ws.state
        state["done"], state["summary"] = True, "기소 반영"
        ws.save_state(state)
        return SessionResult(case_id=case_id, done=True, summary="기소 반영")
    return fake


@needs_chromium
def test_hourly_run_adds_related_news_and_publishes_accepted_edit(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "SEED_DIR", flow_seed(tmp_path))
    report = runner.run(_cfg(tmp_path), "hourly", lake=_lake_with_indictment(),
                        session_runner=_planned(_editing_session("검찰 기소(2026-10-21). 재판 진행")), now=NOW)
    assert report["changed"] == ["hybe-ipo-2019"] and report["reverted"] == []
    stored = json.loads((tmp_path / "store" / "cases" / "hybe-ipo-2019.json").read_text(encoding="utf-8"))
    assert stored["legalStatus"].startswith("검찰 기소") and len(stored["related"]) == 1
    public = json.loads((tmp_path / "public" / "cases" / "hybe-ipo-2019.json").read_text(encoding="utf-8"))
    assert public["legalStatus"] == stored["legalStatus"] and "locked" not in public
    # 같은 기사로 다시 돌면 아무것도 바뀌지 않는다(이미 읽은 page·이미 붙은 관련 보도).
    again = runner.run(_cfg(tmp_path), "hourly", lake=_lake_with_indictment(),
                       session_runner=_planned(_editing_session("다른 값")), now=NOW + timedelta(hours=1))
    assert again["changed"] == [] and again["sessions"] == []


@needs_chromium
def test_session_that_breaks_the_contract_is_not_published(tmp_path, monkeypatch):
    # 검사를 통과하지 못한 세션 결과는 버리고, 관련 보도 추가(코드 몫)만 남는다.
    monkeypatch.setattr(runner, "SEED_DIR", flow_seed(tmp_path))

    def breaking(cfg, ws, **kw):
        case = ws.case()
        case["headline"][0] = "하이브는 투자자에게 거짓말을 했습니다. 이 문장은 단정어를 씁니다."
        ws.save_case(case)
        state = ws.state
        state["done"] = True
        ws.save_state(state)
        return SessionResult(case_id=kw["case_id"], done=True)

    report = runner.run(_cfg(tmp_path), "hourly", lake=_lake_with_indictment(), session_runner=_planned(breaking), now=NOW)
    stored = json.loads((tmp_path / "store" / "cases" / "hybe-ipo-2019.json").read_text(encoding="utf-8"))
    assert "거짓말" not in stored["headline"][0] and len(stored["related"]) == 1
    assert report["changed"] == ["hybe-ipo-2019"]


def test_second_run_is_skipped_while_lease_is_held(tmp_path):
    # 실행이 한 시간을 넘겨 다음 실행과 겹치면 같은 사건을 동시에 고친다.
    store = LocalStore(tmp_path)
    with runner.lease(store, "first", NOW):
        with pytest.raises(runner.RunBusy):
            with runner.lease(store, "second", NOW + timedelta(minutes=5)):
                pass
    with runner.lease(store, "third", NOW):
        pass


def test_locked_seed_overrides_stored_case(tmp_path):
    # 사람이 seed 에 locked 로 넣은 정정본은 에이전트가 고친 저장본보다 우선한다.
    store, seeds = LocalStore(tmp_path / "store"), tmp_path / "seed"
    seeds.mkdir()
    case = seed("hybe-ipo-2019")
    loaded = {}
    (seeds / "hybe-ipo-2019.json").write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
    assert runner.sync_seed(store, loaded, seeds, date(2026, 10, 21)) == ["hybe-ipo-2019"]
    assert runner.sync_seed(store, loaded, seeds, date(2026, 10, 21)) == []  # 잠기지 않은 seed 는 다시 덮지 않는다
    case |= {"locked": True, "legalStatus": "사람 정정"}
    (seeds / "hybe-ipo-2019.json").write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
    assert runner.sync_seed(store, loaded, seeds, date(2026, 10, 21)) == ["hybe-ipo-2019"]
    assert loaded["hybe-ipo-2019"][0]["legalStatus"] == "사람 정정"


def test_config_requires_service_specific_deepseek_key(monkeypatch):
    # 다른 용도 키로 대체하지 않는다(키별 사용량·차단이 섞인다).
    from edge_truth_agent.config import ConfigError
    monkeypatch.setenv("TRUTH_STORE", "x")
    monkeypatch.setenv("TRUTH_PUBLISH", "y")
    monkeypatch.delenv("DEEPSEEK_RESIST_NEWS_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "other")
    with pytest.raises(ConfigError, match="DEEPSEEK_RESIST_NEWS_API_KEY"):
        Config.from_env()
    assert replace  # dataclasses.replace 사용 확인용(정적 분석 경고 방지)


def _failing_session(cfg, ws, **kw):
    raise RuntimeError("모델 API 장애")


@needs_chromium
def test_failed_session_keeps_trigger_articles_for_the_next_run(tmp_path, monkeypatch):
    # 장애 시간에 들어온 '기소' 기사를 말뭉치에만 넣고 버리면 다시는 세션이 열리지 않는다.
    monkeypatch.setattr(runner, "SEED_DIR", flow_seed(tmp_path))
    lake = _lake_with_indictment()
    first = runner.run(_cfg(tmp_path), "hourly", lake=lake, session_runner=_planned(_failing_session), now=NOW)
    assert first["failed"] == ["hybe-ipo-2019"]
    second = runner.run(_cfg(tmp_path), "hourly", lake=lake, session_runner=_planned(_editing_session("검찰 기소(2026-10-21)")),
                        now=NOW + timedelta(hours=1))
    assert second["newArticles"] == 0 and second["changed"] == ["hybe-ipo-2019"] and second["failed"] == []
    state = json.loads((tmp_path / "store" / "state" / "state.json").read_text(encoding="utf-8"))
    assert state["pending"] == {}


@needs_chromium
def test_trigger_is_dropped_and_reported_after_repeated_failures(tmp_path, monkeypatch):
    # 계속 실패하는 사건을 매시 영원히 다시 부르면 비용만 든다 — 정해진 횟수 뒤 버리고 보고한다.
    monkeypatch.setattr(runner, "SEED_DIR", flow_seed(tmp_path))
    lake = _lake_with_indictment()
    reports = [runner.run(_cfg(tmp_path), "hourly", lake=lake, session_runner=_planned(_failing_session), now=NOW + timedelta(hours=i))
               for i in range(runner.PENDING_TRIES + 1)]
    assert [r["dropped"] for r in reports] == [[]] * runner.PENDING_TRIES + [["hybe-ipo-2019"]]


def test_discover_does_not_consume_new_articles(tmp_path, monkeypatch):
    # 하루 한 번 도는 discover 가 새 기사를 말뭉치에 넣으면 매시 실행이 그 기사를 배정하지 못한다.
    monkeypatch.setattr(runner, "SEED_DIR", flow_seed(tmp_path))
    lake = _lake_with_indictment()
    report = runner.run(_cfg(tmp_path), "discover", lake=lake, session_runner=_failing_session, now=NOW)
    assert report["newArticles"] == 0 and lake.reads == []


def test_publish_restores_missing_changed_files_and_removes_strays(tmp_path):
    # 수동 재게시가 공개 위치를 지우거나 다른 내용으로 덮어도 다음 실행이 사건 묶음과 다시 맞춰야 한다
    # (목록은 에이전트 판, 상세는 seed 판으로 갈라지면 안 된다).
    public, cases = LocalStore(tmp_path / "public"), [seed("hybe-ipo-2019"), seed("geumyang-monlaa")]
    runner.publish(public, cases, NOW)
    expected, _ = public.get("cases/geumyang-monlaa.json")
    public.delete("cases/hybe-ipo-2019.json")
    public.put("cases/geumyang-monlaa.json", b'{"stale": true}')
    public.put("cases/stray.json", b"{}")
    result = runner.publish(public, cases, NOW)
    assert result["removed"] == ["cases/stray.json"]
    assert sorted(public.list("cases/")) == ["cases/geumyang-monlaa.json", "cases/hybe-ipo-2019.json"]
    assert public.get("cases/geumyang-monlaa.json")[0] == expected


def test_model_error_in_json_mode_is_reported():
    # pi 는 json 모드에서 모델 오류에도 종료 코드 0 이다 — 사유가 비면 키 만료가 조용히 지나간다.
    from edge_truth_agent.session import _read_events
    result = SessionResult(case_id="x", done=False)
    _read_events(json.dumps({"type": "message_end", "message": {"role": "assistant", "stopReason": "error",
                                                                 "errorMessage": "401 invalid api key", "usage": {}}}).encode(), result)
    assert "401" in result.error


def test_image_installs_exactly_the_workspace_lock_versions():
    # 빌드 때 의존성을 새로 풀면 같은 커밋의 이미지가 시험한 버전과 달라진다(analysis-engine-v2 와 같은 규칙).
    import tomllib
    root = Path(__file__).resolve().parents[1]
    locked = {p["name"]: p["version"] for p in tomllib.loads((root.parents[2] / "uv.lock").read_text(encoding="utf-8"))["package"]}
    pins = [line.split()[0] for line in (root / "requirements.lock").read_text(encoding="utf-8").splitlines() if line and line[0].isalnum()]
    assert len(pins) > 20
    for pin in pins:
        name, _, number = pin.partition("==")
        assert locked.get(name) == number.split(";")[0], pin
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    assert "--require-hashes -r ./apps/etforca/truth-agent/requirements.lock" in dockerfile and "--no-deps" in dockerfile


def test_cases_beyond_the_session_cap_are_not_dropped_untried(tmp_path, monkeypatch):
    # 세션 상한에 밀려 한 번도 돌지 못한 사건을 '실패'로 세어 버리면 기사가 시도 없이 사라진다.
    monkeypatch.setattr(runner, "SEED_DIR", flow_seed(tmp_path))
    cfg = replace(_cfg(tmp_path), max_sessions=0)
    lake = _lake_with_indictment()
    for hour in range(runner.PENDING_TRIES + 2):
        report = runner.run(cfg, "hourly", lake=lake, session_runner=_planned(_failing_session), now=NOW + timedelta(hours=hour))
        assert report["dropped"] == []
    state = json.loads((tmp_path / "store" / "state" / "state.json").read_text(encoding="utf-8"))
    assert state["pending"]["hybe-ipo-2019"]["tries"] == 0


def test_seed_topics_and_title_win_over_stored(tmp_path):
    # 주제어는 사람이 정한 배정 설정이라 seed 를 좁히면 저장본도 따라가야 한다. 제목도 사람이 검토한 공개 문구라
    # seed 를 고치면(예: 과장된 '확정' 표현을 바로잡으면) 저장본이 따라가야 한다.
    store, seeds = LocalStore(tmp_path / "store"), tmp_path / "seed"
    seeds.mkdir()
    case = seed("hybe-ipo-2019")
    stored = case | {"topics": ["상장"], "title": "하이브, 에이전트가 고친 제목"}
    etag = store.put("cases/hybe-ipo-2019.json", json.dumps(stored, ensure_ascii=False).encode())
    (seeds / "hybe-ipo-2019.json").write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
    loaded = {"hybe-ipo-2019": (stored, etag)}
    assert runner.sync_seed(store, loaded, seeds, date(2026, 10, 21)) == ["hybe-ipo-2019"]
    synced = loaded["hybe-ipo-2019"][0]
    assert synced["topics"] == case["topics"] and synced["title"] == case["title"]


def _discover_setup(tmp_path, monkeypatch):
    """새 사건 후보 기사 하나가 말뭉치에 있고, 렌더·세션 검사는 통과로 둔다(여기서 보는 것은 세션 순서와 상한)."""
    monkeypatch.setattr(runner, "SEED_DIR", flow_seed(tmp_path))
    monkeypatch.setattr(runner, "session_problems", lambda ws: [])
    monkeypatch.setattr(runner, "render", lambda cases, web, focus: {k: {"errors": [], "warnings": []} for k in [*focus, "index"]})
    article = news.from_bigkinds(bk("알파전자 대표 검찰 기소", "검찰이 알파전자 대표를 자본시장법 위반 혐의로 기소했다.",
                                    "https://www.yna.co.kr/view/AKR20261021000200001", "01100101.20261021093000002"))
    from edge_truth_agent import corpus
    corpus.save(LocalStore(tmp_path / "store"), {article.id: article})


def test_discover_registers_up_to_the_daily_limit_in_turn(tmp_path, monkeypatch):
    # 새 사건은 하루 최대 DISCOVER_LIMIT 건. 차례로 돌아야 뒤 세션이 앞 세션이 만든 사건을 보고 같은 사건을 다시 만들지 않는다.
    _discover_setup(tmp_path, monkeypatch)
    seen = []

    def creating(cfg, ws, *, case_id, prompt, **kw):
        seen.append(([c["id"] for c in ws.index()], prompt))
        case = seed("geumyang-monlaa")
        case["id"] = f"agent-case-{len(seen)}"
        ws.save_case(case)
        state = ws.state
        state["done"] = True
        ws.save_state(state)
        return SessionResult(case_id=case_id, done=True)

    report = runner.run(_cfg(tmp_path), "discover", session_runner=creating, now=NOW)
    assert len(seen) == runner.DISCOVER_LIMIT
    assert "agent-case-1" in seen[1][0] and "agent-case-2" in seen[2][0]
    assert "이미 새 사건 2건" in seen[2][1] and "이미" not in seen[0][1]
    assert sorted(report["changed"]) == [f"agent-case-{n}" for n in range(1, runner.DISCOVER_LIMIT + 1)]
    assert report["failed"] == []


def test_discover_stops_when_a_session_registers_nothing(tmp_path, monkeypatch):
    # 등록할 후보가 없다고 끝낸 뒤 같은 후보로 세션을 또 열면 비용만 든다.
    _discover_setup(tmp_path, monkeypatch)
    calls = []

    def nothing(cfg, ws, *, case_id, **kw):
        calls.append(case_id)
        state = ws.state
        state["done"] = True
        ws.save_state(state)
        return SessionResult(case_id=case_id, done=True)

    report = runner.run(_cfg(tmp_path), "discover", session_runner=nothing, now=NOW)
    assert calls == ["new-20261021-1"] and report["failed"] == [] and report["changed"] == []


def test_render_server_answers_extensionless_urls_like_the_site(tmp_path):
    # 사이트 링크는 확장자 없는 주소다(배포가 확장자 없는 키에도 올린다). 렌더 검사 서버가 이를 모르면 링크가 404 인데도 통과한다.
    import urllib.request
    from edge_truth_agent.render import _serve
    (tmp_path / "truth").mkdir()
    (tmp_path / "truth" / "issue.html").write_text("<!doctype html><title>issue</title>", encoding="utf-8")
    with _serve(tmp_path) as base:
        body = urllib.request.urlopen(base + "issue?id=hybe-ipo-2019").read().decode("utf-8")
    assert "<title>issue</title>" in body
