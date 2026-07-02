"""dedup 테스트 — S002 '중복없이 저장'의 런 내 보장."""

from data_pipeline.dedup import Deduper


def test_first_seen_is_new_then_duplicate():
    # WHY: 같은 기사가 여러 심볼 질의에 걸려 와도(BRK.B·JPM 동시 언급 등)
    #      한 런에 한 번만 저장돼야 한다 — 두 번째부터는 False.
    d = Deduper()
    assert d.is_new("id-1") is True
    assert d.is_new("id-1") is False
    assert d.is_new("id-2") is True
    assert d.seen_count == 2
