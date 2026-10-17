"""Omitted revision fields preserve prior judgments; explicit edits still validate."""
from copy import deepcopy

from jsonschema import Draft202012Validator, ValidationError
import pytest

from edge_analysis_v2.agent.output_schema import OUTLOOK, OUTLOOK_REVISION, merge_outlook_revision


def test_partial_objects_preserve_sibling_conditions_and_replace_arrays():
    previous = {'conclusion': {'sentence': 'old', 'change_condition': 'keep', 'supports': [1]}}
    original = deepcopy(previous)
    assert merge_outlook_revision(previous, {}) == previous
    assert merge_outlook_revision(previous, {'conclusion': {'sentence': 'corrected', 'supports': []}}) == {
        'conclusion': {'sentence': 'corrected', 'change_condition': 'keep', 'supports': []}}
    assert previous == original


def test_revision_schema_allows_omission_but_not_null_or_partial_array_entries():
    validator = Draft202012Validator(OUTLOOK_REVISION)
    validator.validate({})
    validator.validate({'conclusion': {'sentence': 'corrected'}})
    for value in ({'conclusion': None}, {'conclusion': {'sentence': None}},
                  {'factors': [{'sentence': 'incomplete'}]}, {'unexpected': True}):
        with pytest.raises(ValidationError):
            validator.validate(value)
    with pytest.raises(ValidationError):
        Draft202012Validator(OUTLOOK).validate({})
