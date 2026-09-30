"""Strict transport boundaries; model and screen schemas remain independent."""
import json
from pathlib import Path

from jsonschema import Draft202012Validator, ValidationError

from edge_analysis_v2.contracts.screen_validation import FORMATS

REQUEST_SCHEMA = json.loads((Path(__file__).parent/'request.schema.json').read_text(encoding='utf-8'))


def decode_json(raw):
    """Reject duplicate keys and nonfinite values before validating a payload."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    def invalid(value):
        raise ValueError('Nonfinite JSON value')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def decode_request(raw):
    """Return a validated execution request with no caller-selected resources."""
    value = decode_json(raw)
    try:
        Draft202012Validator(REQUEST_SCHEMA, format_checker=FORMATS).validate(value)
    except ValidationError as exc:
        raise ValueError('Invalid analysis request: ' + exc.message) from None
    return value
