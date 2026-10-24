"""Literal phrase correction shared by body and screen-field revisions."""
from edge_analysis_v2.tools.execution import ToolInputError


def replace_exact(text, old, new):
    """Replace one unambiguous occurrence, preserving every character outside it."""
    if not isinstance(text, str) or not isinstance(old, str) or not old or not isinstance(new, str) or old == new:
        raise ToolInputError('Replacement requires existing text, nonempty old_text and different string new_text')
    position = text.find(old)
    if position < 0 or text.find(old, position + 1) >= 0:
        raise ToolInputError('old_text must match exactly once in the selected text')
    return text.replace(old, new, 1)
