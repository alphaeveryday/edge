"""Persist completed screen publications independently of tool audit commits."""

from datetime import datetime
from uuid import uuid4

from psycopg import sql
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from . import schemas
from .body_changes import KST
from .audited_execution import ToolInputError


class PublicationStore:
    """Save complete analyses, retaining old publications and tool evidence.

    Args:
        connection: Caller-owned idle autocommit PostgreSQL connection.
        final_tool_names: Explicit allowed final-evidence function names.
    """

    def __init__(self, connection, *, final_tool_names=frozenset()):
        self.connection = connection
        self.final_tool_names = frozenset(final_tool_names)
        self._idle()

    def _idle(self):
        if not self.connection.autocommit or self.connection.info.transaction_status != TransactionStatus.IDLE:
            raise ValueError("Publication store requires idle autocommit connection")

    def _table(self, kind):
        if kind not in ("movement", "outlook"):
            raise ValueError("Unknown analysis kind")
        return sql.Identifier(kind + "_analyses")

    def begin(self, kind, analysis_id, etf_code, analysis_at, previous_id=None):
        """Create an idempotent execution parent before any audited tool call."""
        self._idle()
        table = self._table(kind)
        schemas.text(analysis_id)
        schemas.text(etf_code)
        if not isinstance(analysis_at, datetime) or analysis_at.utcoffset() is None:
            raise ValueError("analysis_at requires timezone")
        with self.connection.transaction(), self.connection.cursor(row_factory=dict_row) as cur:
            if previous_id:
                cur.execute(sql.SQL("SELECT * FROM {} WHERE analysis_id=%s").format(table), (previous_id,))
                previous = cur.fetchone()
                if not previous or previous["status"] != "completed" or previous["etf_code"] != etf_code or previous["analysis_at"] >= analysis_at:
                    raise ValueError("Invalid previous publication")
            columns = ["analysis_id", "etf_code", "analysis_at", "previous_analysis_id"]
            values = [analysis_id, etf_code, analysis_at, previous_id]
            if kind == "movement":
                columns.append("trading_date")
                values.append(analysis_at.astimezone(KST).date())
            cur.execute(sql.SQL("INSERT INTO {} ({}) VALUES ({}) ON CONFLICT DO NOTHING").format(
                table, sql.SQL(",").join(map(sql.Identifier, columns)), sql.SQL(",").join(sql.Placeholder() for _ in values)), values)
            cur.execute(sql.SQL("SELECT * FROM {} WHERE analysis_id=%s").format(table), (analysis_id,))
            row = cur.fetchone()
            if any(row[key] != value for key, value in zip(columns, values)):
                raise ValueError("Analysis identity already has different inputs")
            return row

    def _analysis(self, cur, kind, identity):
        cur.execute(sql.SQL("SELECT * FROM {} WHERE analysis_id=%s FOR UPDATE").format(self._table(kind)), (identity,))
        row = cur.fetchone()
        if row is None or row["status"] == "failed":
            raise ValueError("Analysis is missing or failed")
        # Serializes publications for one ETF without requiring another table.
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (kind + ":" + row["etf_code"],))
        return row

    def _evidence(self, cur, ids, analysis):
        for identity in schemas.references(ids):
            cur.execute("""SELECT r.*, d.function_name, COALESCE(m.etf_code,o.etf_code) AS etf_code,
                COALESCE(m.analysis_at,o.analysis_at) AS analysis_at,
                COALESCE(m.status,o.status) AS analysis_status
                FROM tool_runs r JOIN tool_definitions d USING(tool_id)
                LEFT JOIN movement_analyses m ON r.movement_analysis_id=m.analysis_id
                LEFT JOIN outlook_analyses o ON r.outlook_analysis_id=o.analysis_id
                WHERE r.tool_run_id=%s""", (identity,))
            run = cur.fetchone()
            if (not run or run["status"] != "completed"
                    or run["etf_code"] != analysis["etf_code"] or run["analysis_at"] > analysis["analysis_at"]
                    or (analysis["analysis_id"] not in (run["movement_analysis_id"], run["outlook_analysis_id"])
                        and run["analysis_status"] != "completed")):
                raise ToolInputError(f"Evidence {identity!r} is missing, foreign, future, failed, or not final-eligible")
            # Retired tools remain valid only as evidence from completed older publications.
            archived = (run['function_name'] in {'get_factor_metrics', 'get_chart_metrics'}
                and run['analysis_status'] == 'completed'
                and analysis['analysis_id'] not in (run['movement_analysis_id'], run['outlook_analysis_id']))
            if run["function_name"] not in self.final_tool_names and not archived:
                hint = ("Call get_issue_evidence(include_body=false) and use its new tool_run_id."
                        if run["function_name"] == "search_news_threads"
                        else "Use a successful final calculation or evidence tool_run_id.")
                raise ToolInputError(f"Evidence {identity!r} from {run['function_name']} is not final-eligible. {hint}")
            if run["function_name"] == "get_issue_evidence" and run["arguments"].get("include_body") is not False:
                raise ToolInputError(f"Evidence {identity!r}: final news evidence must exclude article body. "
                                     "Call get_issue_evidence(include_body=false) and use its new tool_run_id.")

    def validate_outlook_body_evidence(self, identity, body):
        """Reject invalid draft references before a successful edit is returned.

        Args:
            identity: Running outlook execution identifier.
            body: Candidate BodyEditor result, including derived updates.

        Raises:
            ToolInputError: A reference cannot be used as final evidence.
            ValueError: The outlook is missing or no longer running.
        """
        self._idle()
        with self.connection.transaction(), self.connection.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM outlook_analyses WHERE analysis_id=%s", (identity,))
            analysis = cur.fetchone()
            if not analysis or analysis['status'] != 'running':
                raise ValueError('Running outlook required for draft validation')
            for item in body['items'] + body['updates']['items']:
                self._evidence(cur, item['tool_run_ids'], analysis)

    def save_movement(self, identity, response):
        """Save new immutable items and select at most five same-day items."""
        self._idle()
        with self.connection.transaction(), self.connection.cursor(row_factory=dict_row) as cur:
            analysis = self._analysis(cur, "movement", identity)
            if analysis["status"] == "completed":
                return self._movement(cur, identity)
            schemas.movement(response)
            cur.execute("""WITH RECURSIVE history AS (
                SELECT analysis_id,previous_analysis_id FROM movement_analyses WHERE analysis_id=%s
                UNION ALL SELECT a.analysis_id,a.previous_analysis_id FROM movement_analyses a
                JOIN history h ON a.analysis_id=h.previous_analysis_id
                ) SELECT i.* FROM movement_items i JOIN history h USING(analysis_id)
                JOIN movement_analyses a USING(analysis_id)
                WHERE a.etf_code=%s AND a.trading_date=%s AND a.status='completed' AND a.analysis_at<=%s""",
                        (analysis["previous_analysis_id"], analysis["etf_code"], analysis["trading_date"], analysis["analysis_at"]))
            existing = {row["item_id"] for row in cur.fetchall()}
            mapped = {}
            for item in response["new_items"]:
                if item["candidate_id"] in existing:
                    raise ValueError("Candidate ID collides with existing item")
                self._evidence(cur, item["tool_run_ids"], analysis)
                mapped[item["candidate_id"]] = uuid4().hex
                cur.execute("""INSERT INTO movement_items
                    (item_id,analysis_id,type,title_keyword,sentence,sentiment,source_as_of,tool_run_ids)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (mapped[item["candidate_id"]], identity, item["type"], item["title_keyword"], item["sentence"],
                     item["sentiment"], analysis["analysis_at"], item["tool_run_ids"]))
            if any(ref not in existing and ref not in mapped for ref in response["selected_item_ids"]):
                raise ValueError("Selection contains unknown or other-day item")
            selected = [mapped.get(ref, ref) for ref in response["selected_item_ids"]]
            cur.execute("""SELECT * FROM movement_analyses WHERE analysis_id=%s AND trading_date=%s
                AND status='completed'""", (analysis["previous_analysis_id"], analysis["trading_date"]))
            previous = cur.fetchone()
            unchanged = previous and (selected == previous["selected_item_ids"] or not selected)
            if unchanged:
                selected, summary, published = previous["selected_item_ids"], previous["summary"], previous["published_at"]
            else:
                summary, published = response["summary"], datetime.now(KST) if selected else None
            cur.execute("""UPDATE movement_analyses SET summary=%s,selected_item_ids=%s,status='completed',published_at=%s
                WHERE analysis_id=%s""", (summary, selected, published, identity))
            return self._movement(cur, identity)

    def _movement(self, cur, identity):
        cur.execute("SELECT summary,selected_item_ids FROM movement_analyses WHERE analysis_id=%s AND status='completed'", (identity,))
        row = cur.fetchone()
        if row is None:
            return None
        cur.execute("""SELECT item_id,type,title_keyword,sentence,sentiment,tool_run_ids FROM movement_items
            WHERE item_id=ANY(%s)""", (row["selected_item_ids"],))
        lookup = {item.pop("item_id"): item for item in cur.fetchall()}
        return {"summary": row["summary"], "items": [lookup[ref] for ref in row["selected_item_ids"]]}

    def get_movement(self, identity):
        """Assemble one completed movement from stored rows only."""
        self._idle()
        with self.connection.transaction(), self.connection.cursor(row_factory=dict_row) as cur:
            return self._movement(cur, identity)

    def save_outlook(self, identity, features, body, *, factor_details=None):
        """Atomically publish the edited body and complete independent features.

        Args:
            identity: Running outlook execution ID.
            features: outlook, summary_card, factors, and conclusion objects.
            body: BodyEditor.result(), never a separate agent-authored delta.
            factor_details: Optional metrics and issue detail for the same edition.
        """
        self._idle()
        with self.connection.transaction(), self.connection.cursor(row_factory=dict_row) as cur:
            analysis = self._analysis(cur, "outlook", identity)
            if analysis["status"] == "completed":
                return self._outlook(cur, identity)
            schemas.outlook(features, body)
            if body["updates"]["date"] != analysis["analysis_at"].astimezone(KST).date().isoformat():
                raise ValueError("Update date must match analysis date")
            if (body["mode"] == "create") != (analysis["previous_analysis_id"] is None):
                raise ValueError("Body mode disagrees with previous publication")
            for position, item in enumerate(body["items"]):
                self._evidence(cur, item["tool_run_ids"], analysis)
                cur.execute("""INSERT INTO outlook_items
                    (row_id,analysis_id,item_id,section,position,title_keyword,bullets,source_as_of,tool_run_ids)
                    VALUES(%s,%s,%s,'detail',%s,%s,%s,%s,%s)""",
                    (uuid4().hex, identity, item["id"], position, item["title_keyword"], Jsonb(item["sentences"]),
                     analysis["analysis_at"], item["tool_run_ids"]))
            for position, item in enumerate(body["updates"]["items"]):
                self._evidence(cur, item["tool_run_ids"], analysis)
                cur.execute("""INSERT INTO outlook_items
                    (row_id,analysis_id,item_id,section,change_type,position,title_keyword,sentence,tool_run_ids)
                    VALUES(%s,%s,%s,'update',%s,%s,%s,%s,%s)""",
                    (uuid4().hex, identity, item["id"], item["change_type"], position, item["title_keyword"],
                     item["sentence"], item["tool_run_ids"]))
            for factor in features["factors"]:
                cur.execute("""INSERT INTO outlook_factors(row_id,analysis_id,type,sticker,sentence)
                    VALUES(%s,%s,%s,%s,%s)""", (uuid4().hex, identity, factor["type"], factor["sticker"], factor["sentence"]))
            conclusion = features["conclusion"]
            for field, kind in (("supports", "support"), ("burdens", "burden")):
                for position, item in enumerate(conclusion[field]):
                    self._evidence(cur, item["tool_run_ids"], analysis)
                    cur.execute("""INSERT INTO outlook_conclusion_keywords(row_id,analysis_id,kind,position,label,tool_run_ids)
                        VALUES(%s,%s,%s,%s,%s,%s)""", (uuid4().hex, identity, kind, position, item["label"], item["tool_run_ids"]))
            if factor_details is not None:
                from .factor_store import save_factor_details
                for item in factor_details['issue']['items']:
                    self._evidence(cur, item['tool_run_ids'], analysis)
                save_factor_details(self.connection, identity, **factor_details)
            cur.execute("""UPDATE outlook_analyses SET status='completed',published_at=%s,outlook_sticker=%s,
                summary_title=%s,summary=%s,detail_title=%s,detail_mode=%s,conclusion_title=%s,
                conclusion_sentence=%s,change_condition=%s WHERE analysis_id=%s""",
                (datetime.now(KST), features["outlook"]["direction"], features["summary_card"]["title"],
                 features["summary_card"]["summary"], body["title"], body["mode"], conclusion["title"],
                 conclusion["sentence"], conclusion.get("change_condition"), identity))
            return self._outlook(cur, identity)

    def _outlook(self, cur, identity):
        cur.execute("SELECT * FROM outlook_analyses WHERE analysis_id=%s AND status='completed'", (identity,))
        row = cur.fetchone()
        if row is None:
            return None
        detail = {"title": row["detail_title"], "items": [], "updates": {
            "date": row["analysis_at"].astimezone(KST).date().isoformat(), "items": []}}
        cur.execute("SELECT * FROM outlook_items WHERE analysis_id=%s ORDER BY position", (identity,))
        for item in cur.fetchall():
            if item["section"] not in ("detail", "update"):
                continue
            value = {"id": item["item_id"], "title_keyword": item["title_keyword"], "tool_run_ids": item["tool_run_ids"]}
            if item["section"] == "detail":
                value["sentences"] = item["bullets"]
                detail["items"].append(value)
            else:
                value.update(change_type=item["change_type"], sentence=item["sentence"])
                detail["updates"]["items"].append(value)
        cur.execute("SELECT type,sticker,sentence FROM outlook_factors WHERE analysis_id=%s", (identity,))
        factors = {factor["type"]: factor for factor in cur.fetchall()}
        conclusion = {"title": row["conclusion_title"], "supports": [], "burdens": [], "sentence": row["conclusion_sentence"]}
        if row["change_condition"] is not None:
            conclusion["change_condition"] = row["change_condition"]
        cur.execute("SELECT kind,label,tool_run_ids FROM outlook_conclusion_keywords WHERE analysis_id=%s ORDER BY position", (identity,))
        for item in cur.fetchall():
            field = "supports" if item.pop("kind") == "support" else "burdens"
            conclusion[field].append(item)
        return {"outlook": {"direction": row["outlook_sticker"]},
                "summary_card": {"title": row["summary_title"], "summary": row["summary"]},
                "detail": detail, "factors": [factors[factor] for factor in schemas.FACTORS], "conclusion": conclusion}

    def get_outlook(self, identity):
        """Assemble the independent outlook feature objects from committed rows."""
        self._idle()
        with self.connection.transaction(), self.connection.cursor(row_factory=dict_row) as cur:
            return self._outlook(cur, identity)

    def fail(self, kind, identity, error):
        """Mark an unfinished execution failed without changing a publication."""
        self._idle()
        with self.connection.transaction():
            self.connection.execute(sql.SQL("UPDATE {} SET status='failed',error_message=%s WHERE analysis_id=%s AND status='running'").format(self._table(kind)), (str(error), identity))
