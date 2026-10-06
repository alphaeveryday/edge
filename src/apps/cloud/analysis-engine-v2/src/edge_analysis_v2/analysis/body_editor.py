"""Apply outlook topic edits without rewriting untouched topics."""

from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from edge_analysis_v2.contracts.outlook_limits import violations
from edge_analysis_v2.tools.execution import ToolInputError


KST = timezone(timedelta(hours=9))


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Expected nonempty text")
    return value


def _topic(raw, *, added=False):
    if set(raw) - {"id", "title_keyword", "sentences", "tool_run_ids", "updated_sentence_numbers"}:
        raise ValueError("Unknown topic field")
    numbers = raw.get("updated_sentence_numbers", [])
    sentences = raw["sentences"]
    if not isinstance(sentences, list) or not sentences:
        raise ValueError("Topic requires sentences")
    if (not isinstance(numbers, list) or any(type(n) is not int or not 1 <= n <= len(sentences) for n in numbers)
            or len(set(numbers)) != len(numbers)):
        raise ValueError("Invalid updated sentence numbers")
    refs = raw["tool_run_ids"]
    if not isinstance(refs, list) or not refs or len(set(refs)) != len(refs):
        raise ValueError("Topic requires unique tool_run_ids")
    return {"id": _text(raw["id"]), "title_keyword": _text(raw["title_keyword"]),
            "sentences": [{"sentence": _text(text), "is_updated": added or i in numbers}
                          for i, text in enumerate(sentences, 1)],
            "tool_run_ids": [_text(ref) for ref in refs]}


def _different(left, right):
    return (left["title_keyword"] != right["title_keyword"]
            or Counter(s["sentence"] for s in left["sentences"])
            != Counter(s["sentence"] for s in right["sentences"]))


class BodyEditor:
    """Keep a draft and derive same-day updates from explicit topic changes.

    Args:
        base: Previous assembled detail, or None for first publication.
        analysis_at: Timezone-aware fixed analysis cutoff.
    """

    def __init__(self, base: dict | None, analysis_at: datetime):
        if analysis_at.utcoffset() is None:
            raise ValueError("analysis_at must include timezone")
        self.date = analysis_at.astimezone(KST).date().isoformat()
        self.base = deepcopy(base)
        self.draft = deepcopy(base) if base is not None else {"title": "", "items": []}
        updates = self.draft.get("updates", {})
        self.updates = deepcopy(updates.get("items", [])) if updates.get("date") == self.date else []
        if updates.get("date") != self.date:
            for item in self.draft["items"]:
                for sentence in item["sentences"]:
                    sentence["is_updated"] = False
        self.mode = "create" if base is None else "update"

    def write(self, title: str, items: list) -> dict:
        """Replace the complete draft, preserving IDs for retained topics."""
        existing = {item["id"] for item in (self.base or {}).get("items", [])}
        normalized = [_topic(item, added=self.base is not None and item["id"] not in existing) for item in items]
        return self._commit({"title": _text(title), "items": normalized}, "create" if self.base is None else "rewrite")

    def apply(self, changes: list, title: str | None = None, item_order: list | None = None) -> dict:
        """Apply one atomic batch of additions, field edits, and removals."""
        if self.base is None and not self.draft["title"]:
            raise ValueError("First publication requires write")
        draft = deepcopy(self.draft)
        touched = set()
        for change in changes:
            identity, action = change["id"], change["action"]
            if identity in touched:
                raise ValueError("Duplicate topic in change batch")
            touched.add(identity)
            current = next((item for item in draft["items"] if item["id"] == identity), None)
            if action == "add":
                if current is not None:
                    raise ValueError("Topic already exists")
                draft["items"].append(_topic({key: value for key, value in change.items() if key != "action"}, added=True))
            elif action == "remove":
                if current is None or set(change) != {"action", "id"}:
                    raise ValueError("Removal requires existing topic ID only")
                draft["items"].remove(current)
            elif action == "update":
                if current is None:
                    raise ValueError("Unknown topic")
                if set(change) & {"title_keyword", "sentences"} and "tool_run_ids" not in change:
                    raise ValueError("Changed text requires tool_run_ids")
                raw = {"id": identity, "title_keyword": current["title_keyword"],
                       "sentences": [s["sentence"] for s in current["sentences"]],
                       "tool_run_ids": current["tool_run_ids"]}
                raw.update({key: value for key, value in change.items() if key != "action"})
                replacement = _topic(raw)
                if "sentences" not in change and "updated_sentence_numbers" not in change:
                    replacement["sentences"] = current["sentences"]
                draft["items"][draft["items"].index(current)] = replacement
            else:
                raise ValueError("Unknown edit action")
        if title is not None:
            draft["title"] = _text(title)
        if item_order is not None:
            lookup = {item["id"]: item for item in draft["items"]}
            if len(item_order) != len(lookup) or set(item_order) != set(lookup):
                raise ValueError("item_order must contain all topic IDs once")
            draft["items"] = [lookup[identity] for identity in item_order]
        return self._commit(draft, self.mode)

    def _commit(self, draft, mode):
        over = violations({'detail': draft})
        if over:
            # A body inherited from before the limits existed is over in many topics; one rewrite is the short way out.
            hint = '; rewrite the whole body with write_outlook_body' if mode == 'update' else ''
            raise ToolInputError('Body exceeds limits: ' + ', '.join(
                f"{v['location']} {v['actual']}>{v['limit']} {v['kind']}" for v in over[:8])
                + (f' and {len(over) - 8} more' if len(over) > 8 else '') + hint)
        current = {item["id"]: item for item in draft["items"]}
        if len(current) != len(draft["items"]) or len(current) > 15:
            raise ValueError("Expected at most 15 unique topics")
        base = {item["id"]: item for item in (self.base or {}).get("items", [])}
        changed = set(base) ^ set(current)
        changed.update(identity for identity in base.keys() & current.keys() if _different(base[identity], current[identity]))
        if mode == "update" and len(changed) >= 10:
            raise ValueError("Ten changed topics require rewrite")
        previous = {item["id"]: item for item in self.draft["items"]}
        for identity in previous.keys() & current.keys():
            flags = {}
            for sentence in previous[identity]["sentences"]:
                flags.setdefault(sentence["sentence"], []).append(sentence["is_updated"])
            for sentence in current[identity]["sentences"]:
                if sentence["sentence"] in flags:
                    retained = flags[sentence["sentence"]]
                    sentence["is_updated"] = retained.pop(0) if retained else False
        if self.base is None:
            for item in draft["items"]:
                for sentence in item["sentences"]:
                    sentence["is_updated"] = False
        updates = {item["id"]: deepcopy(item) for item in self.updates}
        if self.base is not None:
            for identity, item in previous.items():
                if identity not in current:
                    updates.pop(identity, None)
                    updates[identity] = {"id": identity, "change_type": "deleted", "title_keyword": item["title_keyword"],
                                         "sentence": None, "tool_run_ids": item["tool_run_ids"]}
            for identity, item in current.items():
                added = identity not in previous
                if added or _different(previous[identity], item):
                    sentences = [s["sentence"] for s in item["sentences"] if added or s["is_updated"]]
                    updates[identity] = {"id": identity, "change_type": "added" if added else "modified",
                                         "title_keyword": item["title_keyword"], "sentence": "\n".join(sentences) or None,
                                         "tool_run_ids": item["tool_run_ids"]}
                elif identity in updates:
                    updates[identity]["tool_run_ids"] = item["tool_run_ids"]
        ordered = [updates[identity] for identity in current if identity in updates]
        ordered.extend(item for identity, item in updates.items() if identity not in current)
        self.draft, self.mode, self.updates = deepcopy(draft), mode, ordered
        return self.result()

    def result(self) -> dict:
        """Return an isolated draft without publishing or writing evidence."""
        return {"title": self.draft["title"], "items": deepcopy(self.draft["items"]),
                "updates": {"date": self.date, "items": deepcopy(self.updates)}, "mode": self.mode}
