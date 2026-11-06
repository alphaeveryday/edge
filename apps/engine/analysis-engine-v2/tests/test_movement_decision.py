"""A movement revision says whether it keeps the previous publication, so "nothing changed" is never a guess."""
from jsonschema import Draft202012Validator, ValidationError
import pytest

from edge_analysis_v2.agent.output_schema import MOVEMENT, MOVEMENT_REVISION
from edge_analysis_v2.contracts.publication_validation import movement

KEEP = {'decision': 'keep', 'new_items': [], 'selected_item_ids': [], 'summary': None}


def test_only_the_revision_schema_requires_a_decision():
    Draft202012Validator(MOVEMENT_REVISION).validate(KEEP)
    with pytest.raises(ValidationError):
        Draft202012Validator(MOVEMENT_REVISION).validate({key: value for key, value in KEEP.items() if key != 'decision'})
    # The first publication of the day has nothing to keep.
    with pytest.raises(ValidationError):
        Draft202012Validator(MOVEMENT).validate(KEEP)


def test_keep_is_the_empty_answer_and_names_what_to_send_otherwise():
    movement(KEEP)
    # 0167A0 11-06 12:29: "no new material, keep the four items" sent the four ids with a null summary.
    with pytest.raises(ValueError, match='decision=keep requires empty'):
        movement(KEEP | {'selected_item_ids': ['a', 'b', 'c', 'd']})
    with pytest.raises(ValueError, match='decision=keep requires empty'):
        movement(KEEP | {'summary': '그대로예요.'})


def test_revise_must_carry_the_selection_it_publishes():
    with pytest.raises(ValueError, match='send decision=keep'):
        movement(KEEP | {'decision': 'revise'})
    with pytest.raises(ValueError, match='selected_item_ids requires a nonempty summary'):
        movement(KEEP | {'decision': 'revise', 'selected_item_ids': ['a']})
    movement(KEEP | {'decision': 'revise', 'selected_item_ids': ['a'], 'summary': '계약이 미뤄졌어요.'})
    with pytest.raises(ValueError, match='keep or revise'):
        movement(KEEP | {'decision': None})
