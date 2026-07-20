"""observability — stable_id 결정성(멱등 upsert·계보의 재료)."""

from edge_analysis.observability import stable_id


def test_stable_id_is_deterministic():
    # 같은 입력 → 같은 id. 재실행 시 ON CONFLICT upsert 가 수렴한다.
    assert stable_id("cob", "pmt_X") == stable_id("cob", "pmt_X")


def test_stable_id_differs_for_different_input():
    # 다른 재료 → 다른 id. 같으면 서로 다른 트리거의 계보가 한 행에 충돌한다.
    assert stable_id("cob", "a") != stable_id("cob", "b")
