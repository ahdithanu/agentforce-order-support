"""STDM record sources. Only the live source knows about Salesforce CLI or SQL."""
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


# The same projection is used for Data Cloud rows and synthetic STDM fixtures.
TABLES = {
    "sessions": ("ssot__AiAgentSession__dlm", {
        "id": "ssot__Id__c", "end_type": "ssot__AiAgentSessionEndType__c",
        "started": "ssot__StartTimestamp__c",
    }),
    "interactions": ("ssot__AiAgentInteraction__dlm", {
        "id": "ssot__Id__c", "session": "ssot__AiAgentSessionId__c",
        "topic": "ssot__TopicApiName__c", "type": "ssot__AiAgentInteractionType__c",
        "started": "ssot__StartTimestamp__c", "ended": "ssot__EndTimestamp__c",
    }),
    "steps": ("ssot__AiAgentInteractionStep__dlm", {
        "interaction": "ssot__AiAgentInteractionId__c", "type": "ssot__AiAgentInteractionStepType__c",
        "name": "ssot__Name__c", "input": "ssot__InputValueText__c",
        "output": "ssot__OutputValueText__c", "error": "ssot__ErrorMessageText__c",
    }),
    "messages": ("ssot__AiAgentInteractionMessage__dlm", {
        "interaction": "ssot__AiAgentInteractionId__c", "type": "ssot__AiAgentInteractionMessageType__c",
        "text": "ssot__ContentText__c", "sent": "ssot__MessageSentTimestamp__c",
    }),
}


@dataclass
class TraceRecords:
    sessions: list[dict]
    interactions: list[dict]
    steps: list[dict]
    messages: list[dict]


class DataCloudSource:
    def __init__(self, org):
        self.org = org

    def _rest(self, *rest_args):
        result = subprocess.run(
            ["sf", "api", "request", "rest", *rest_args, "--target-org", self.org],
            capture_output=True, text=True,
        )
        if result.returncode:
            raise ValueError(f"Salesforce CLI request failed (exit {result.returncode})")
        try:
            # sf can prepend informational text to the JSON response.
            return json.JSONDecoder().raw_decode(result.stdout[result.stdout.find("{"):])[0]
        except json.JSONDecodeError as exc:
            raise ValueError("Salesforce CLI returned an invalid JSON response") from exc

    def sql(self, query):
        """Run a Data Cloud SQL query and page through every row."""
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump({"sql": query}, fh)
            body = fh.name
        try:
            data = self._rest("/services/data/v67.0/ssot/query-sql", "--method", "POST", "--body", f"@{body}")
        finally:
            os.unlink(body)
        if "metadata" not in data:
            raise ValueError("Data Cloud query failed: missing metadata")
        cols = [m["name"] for m in data["metadata"]]
        rows, total, qid = list(data["data"]), data["status"]["rowCount"], data["status"]["queryId"]
        while len(rows) < total:
            page = self._rest(f"/services/data/v67.0/ssot/query-sql/{qid}/rows?offset={len(rows)}&rowLimit=5000")
            if not page.get("data"):
                raise ValueError(f"Data Cloud paging stopped at {len(rows)}/{total} rows")
            rows += page["data"]
        return [dict(zip(cols, row)) for row in rows]

    def load(self):
        return TraceRecords(**{
            name: self.sql("SELECT " + ", ".join(f'"{column}" AS {alias}' for alias, column in fields.items())
                           + f' FROM "{table}"')
            for name, (table, fields) in TABLES.items()
        })


class FixtureSource:
    """Read raw STDM field names and a fixed clock from a local JSON fixture."""
    def __init__(self, path):
        blob = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(blob, dict):
            raise ValueError("Fixture must be a JSON object")
        try:
            self.as_of = datetime.fromisoformat(blob["as_of"].replace("Z", "+00:00"))
        except (KeyError, AttributeError, ValueError) as exc:
            raise ValueError("Fixture needs an ISO 8601 as_of timestamp with a timezone") from exc
        if self.as_of.utcoffset() is None:
            raise ValueError("Fixture as_of must include a timezone")
        records = {}
        for name, (table, fields) in TABLES.items():
            rows = blob.get(table)
            if not isinstance(rows, list):
                raise ValueError(f"Fixture {table} must be an array")
            records[name] = []
            for index, row in enumerate(rows):
                if not isinstance(row, dict) or any(column not in row for column in fields.values()):
                    raise ValueError(f"Fixture {table} row {index} is missing required STDM fields")
                if any(value is not None and not isinstance(value, str) for value in
                       (row[column] for column in fields.values())):
                    raise ValueError(f"Fixture {table} row {index} fields must be strings or null")
                records[name].append({alias: row[column] for alias, column in fields.items()})
        self.records = TraceRecords(**records)

    def load(self):
        return self.records
