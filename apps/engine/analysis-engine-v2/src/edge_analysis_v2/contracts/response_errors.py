"""Machine-readable validation feedback shared by admission and publication."""
import json
from jsonschema import Draft202012Validator


# Domain rules complement JSON Schema without building a second validation framework.
REPAIRS = {
    'forbidden': 'Remove this property.',
    'required': 'Add the required properties with valid values.',
    'additionalProperties': 'Remove properties not declared in expected.',
    'enum': 'Use one of the values in expected exactly; do not invent an ID.',
    'conflict': 'Use one consistent value or edit mechanism for this target.',
    'unique': 'Submit this target only once.',
    'uniqueItems': 'Remove duplicates.',
    'exists': 'Select an existing target from the previous publication.',
    'exactMatch': 'Copy old_text from the source so it matches exactly once; use different new_text.',
}


class ResponseError(ValueError):
    """A correctable response defect, located in the submitted JSON."""
    def __init__(self, path, rule, expected, actual, *, target=None):
        self.issue = dict(path=path, rule=rule, expected=expected, actual=actual)
        if target is not None:
            self.issue['target'] = target
        self.issue['repair'] = REPAIRS.get(rule, 'Change the value at path to satisfy expected; preserve other fields.')
        super().__init__(json.dumps(self.issue, ensure_ascii=False, default=str))


def validate(value, schema, path, *, target=None):
    """Translate JSON Schema failures into the same feedback as domain rules."""
    error = next(Draft202012Validator(schema).iter_errors(value), None)
    if error:
        suffix = ''.join(f'[{part}]' if isinstance(part, int) else f'.{part}' for part in error.path)
        expected = list(error.schema.get('properties', {})) if error.validator == 'additionalProperties' else error.validator_value
        raise ResponseError(path + suffix, error.validator, expected, error.instance, target=target)
