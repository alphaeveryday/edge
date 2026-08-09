"""§7 "블록은 항상 나간다 — 없으면 없다고 말한다" 전수 검증."""
from __future__ import annotations

import pytest

from edge_analysis.statics.evidence_absent import (
    ABSENCE_TEXT, BLOCKS, UnknownBlock, render_block)


class TestAbsenceBlocks:
    @pytest.mark.parametrize("block", BLOCKS)
    def test_content_present_wins(self, block):
        assert render_block(block, "실제 내용") == "실제 내용"

    @pytest.mark.parametrize("block", BLOCKS)
    @pytest.mark.parametrize("content", [None, "", "   "])
    def test_absent_or_blank_falls_back_to_fixed_phrase(self, block, content):
        assert render_block(block, content) == ABSENCE_TEXT[block]

    def test_all_four_blocks_have_distinct_phrases(self):
        """"안 봤다" 와 "봤는데 없었다" 를 구분하는 문구이므로 서로 같으면 안 된다."""
        assert len(set(ABSENCE_TEXT.values())) == len(BLOCKS) == 4

    def test_unknown_block_name_rejected(self):
        with pytest.raises(UnknownBlock):
            render_block("헤더", "x")
