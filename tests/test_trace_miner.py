"""Offline regression coverage for the production trace to eval draft loop."""
import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import trace_miner as miner
from scripts.trace_sources import DataCloudSource, FixtureSource, TABLES

FIXTURE = ROOT / "tests/fixtures/stdm_synthetic.json"
EXPECTED_FLAGS = {
    "kb_decline": 3, "action_error": 2, "rephrase": 2, "verify_failed": 2, "off_topic": 2,
    "fake_progress": 1, "low_adherence": 1, "slow": 1, "ungrounded": 1,
    "unrequested_escalation": 1, "dropped": 1, "escalated": 1,
}


class MiningTests(unittest.TestCase):
    def setUp(self):
        self.source = FixtureSource(FIXTURE)
        self.records = self.source.load()
        self.rows, self.session_count = miner.reconstruct(
            self.records, self.source.as_of - timedelta(hours=24),
        )
        self.by_turn = {(r["session"], r["turn"]): r for r in self.rows}

    def mine(self, **kwargs):
        return miner.mine(self.records, as_of=self.source.as_of, source="fixture", **kwargs)

    def test_reconstruction_joins_and_orders_messages_without_mutating_records(self):
        before = copy.deepcopy(self.records)
        rows, count = miner.reconstruct(self.records, self.source.as_of - timedelta(hours=24))
        self.assertEqual((count, len(rows)), (5, 11))
        self.assertEqual(self.records, before)
        self.assertEqual([(r["session"], r["turn"]) for r in rows], [
            ("pubfail1", 1), ("pubfail1", 2), ("pubfail1", 3),
            ("pubknow1", 1), ("pubknow1", 2), ("pubknow1", 3),
            ("pubroute", 1), ("pubroute", 2), ("pubclean", 1), ("pubclean", 2), ("pubhuman", 1),
        ])
        first = rows[0]
        self.assertEqual(first["user"], "Please check the parcel for <email>.")
        self.assertEqual(first["reply"], "Let me check that for <email>, call <number>.")
        self.assertEqual(first["route"], "identity_verification")
        self.assertEqual(first["latency_ms"], 10000)
        self.assertEqual((first["grounded"], first["adherence"], first["resolution"]),
                         ("NOT_GROUNDED", "MEDIUM", "UNRESOLVED"))
        second = rows[1]
        self.assertEqual(second["actions"], ["verify_customer"])
        self.assertEqual(second["latency_ms"], 1000)
        self.assertEqual(second["evidence"]["action_error"],
                         "verify_customer: Cannot verify <email>; call <number>; reference <number>.")

    def test_every_flag_and_negative_controls(self):
        expected = [
            {"fake_progress", "low_adherence", "slow", "ungrounded"},
            {"action_error", "rephrase", "verify_failed"}, {"unrequested_escalation", "dropped"},
            {"kb_decline"}, {"kb_decline", "rephrase"}, {"kb_decline"},
            {"off_topic"}, {"off_topic"}, {"action_error", "verify_failed"}, set(), {"escalated"},
        ]
        self.assertEqual([set(r["flags"]) for r in self.rows], expected)
        # Exactly 8000 ms is not slow; successful action and SMALL_TALK are not failures.
        self.assertEqual(self.by_turn["pubclean", 2]["latency_ms"], 8000)
        mined, _ = self.mine()
        self.assertEqual(mined["summary"]["flags"], EXPECTED_FLAGS)
        self.assertEqual(mined["summary"]["flagged_turns"], 10)
        self.assertNotIn(self.by_turn["pubclean", 2], mined["turns"])

    def test_environment_window_and_agent_filtering(self):
        for env, expected in [("published", (5, 11)), ("preview", (1, 1)), ("all", (6, 12))]:
            with self.subTest(env=env):
                mined, _ = self.mine(env=env)
                self.assertEqual((mined["summary"]["sessions"], mined["summary"]["turns"]), expected)
                self.assertTrue(all(r["session"] not in {"oldpub01", "foreign1", "undated1"}
                                    for r in mined["turns"]))
        mined, candidates = self.mine(env="preview")
        self.assertEqual(mined["summary"]["flags"], {"kb_decline": 1, "ungrounded": 1})
        self.assertEqual(candidates[0]["utterance"], "¿Puedo cambiar el color después de comprar?")
        self.assertEqual(candidates[0]["suite"], "rag-eval")
        recent, _ = self.mine(hours=1)
        self.assertEqual((recent["summary"]["sessions"], recent["summary"]["turns"]), (3, 5))
        empty, drafts = self.mine(hours=0.1)
        self.assertEqual(empty["summary"]["sessions"], 0)
        self.assertEqual(drafts, [])
        self.assertIn("0 flagged, 0 new candidate cases.", miner.digest(empty))

    def test_missing_timestamps_sentinels_and_malformed_step_json(self):
        self.assertIsNone(self.by_turn["pubroute", 1]["adherence"])
        self.assertIsNone(self.by_turn["pubroute", 2]["latency_ms"])
        self.assertIsNone(self.by_turn["pubroute", 2]["resolution"])
        self.assertEqual(miner.parse("{bad"), {})
        self.assertEqual(miner.parse("NOT_SET"), {})
        self.assertEqual(miner.verdict(json.dumps({"mgr.sensitive.step.result": "InstructionAdherence=HIGH"}),
                                       "InstructionAdherence"), "HIGH")

    def test_rephrase_and_dropped_require_unresolved_previous_or_final_turn(self):
        changed = copy.deepcopy(self.records)
        for step in changed.steps:
            if step["interaction"] in {"policy-1", "failure-3"}:
                step["output"] = step["output"].replace("UNRESOLVED", "RESOLVED")
        rows, _ = miner.reconstruct(changed, self.source.as_of - timedelta(hours=24))
        by_turn = {(r["session"], r["turn"]): r for r in rows}
        self.assertNotIn("rephrase", by_turn["pubknow1", 2]["flags"])
        self.assertNotIn("dropped", by_turn["pubfail1", 3]["flags"])
        # An unresolved last turn in a completed session is not dropped.
        self.assertNotIn("dropped", self.by_turn["pubknow1", 3]["flags"])

    def test_error_message_fallback_is_masked(self):
        changed = copy.deepcopy(self.records)
        for step in changed.steps:
            if step["interaction"] == "failure-2" and step["type"] == "ACTION_STEP":
                step["output"] = json.dumps({"actionResponse": {
                    "__action_execution_status__": "error", "errors": [],
                    "error_message": "Contact synthetic.buyer@example.com at 202-555-0101",
                }})
        rows, _ = miner.reconstruct(changed, self.source.as_of - timedelta(hours=24))
        self.assertEqual(rows[1]["evidence"]["action_error"], "verify_customer: Contact <email> at <number>")

    def test_pii_masking_preserves_short_order_numbers_and_policy_values(self):
        for text in ["buyer+tag@example.com", "202-555-0101", "(202) 555-0101", "+1 202-555-0101",
                     "202.555.0101", "2025550101", "123456789012"]:
            with self.subTest(text=text):
                self.assertEqual(miner.mask(text), "<email>" if "@" in text else "<number>")
        safe = "Order A-1001, 30 days, $15, 2026 and 4 digits 1001"
        self.assertEqual(miner.mask(safe), safe)

    def test_deduplication_against_all_five_existing_suite_shapes(self):
        known = miner.known_utterances()
        for name in miner.SUITE_NAMES:
            blob = yaml.safe_load((ROOT / "tests" / name).read_text())
            case = (blob.get("cases") or blob.get("testCases") or blob.get("utterances"))[0]
            user = case.get("u") or case.get("utterance") or case.get("question")
            with self.subTest(suite=name):
                row = dict(self.rows[0], user=miner.mask(user.upper() + "!!!"))
                self.assertEqual(miner.draft_candidates([row], known), [])
        # The functional case contains an email, but the trace has already masked it.
        self.assertEqual(miner.draft_candidates([self.by_turn["pubclean", 1]], known), [])

    def test_deduplication_within_batch_and_digest_only_signals(self):
        row = dict(self.rows[0], user="A new utterance", flags=["slow"])
        actionable = dict(row, flags=["fake_progress"])
        duplicate = dict(actionable, user="  A NEW   UTTERANCE!!! ")
        self.assertEqual(len(miner.draft_candidates([row, actionable, duplicate], set())), 1)
        for flag in ("escalated", "slow", "low_adherence", "verify_failed", "dropped"):
            with self.subTest(flag=flag):
                self.assertEqual(miner.draft_candidates([dict(row, flags=[flag])], set()), [])
        self.assertEqual(miner.draft_candidates([dict(actionable, user="")], set()), [])

    def test_candidate_suites_sources_and_review_placeholders(self):
        mined, candidates = self.mine()
        self.assertEqual(mined["summary"]["candidates"], 5)
        self.assertEqual([(c["source"], c["suite"]) for c in candidates], [
            ("pubfail1#1", "testing-center"), ("pubfail1#3", "routing-set"),
            ("pubknow1#1", "rag-eval"), ("pubroute#1", "routing-set"), ("pubroute#2", "routing-set"),
        ])
        self.assertEqual(candidates[0]["expectedOutcome"], "TODO")
        self.assertEqual(candidates[1]["expected_route"], "TODO")
        self.assertEqual(candidates[2]["kind"], "TODO answerable|unanswerable")
        self.assertEqual(candidates[2]["must_include"], "TODO if answerable")
        self.assertEqual(candidates[0]["utterance"], "Please check the parcel for <email>.")

    def test_outputs_are_deterministic_and_mask_all_written_evidence(self):
        mined, candidates = self.mine()
        reversed_records = copy.deepcopy(self.records)
        for name in TABLES:
            getattr(reversed_records, name).reverse()
        replay, replay_candidates = miner.mine(reversed_records, as_of=self.source.as_of, source="fixture")
        self.assertEqual((mined, candidates), (replay, replay_candidates))
        with tempfile.TemporaryDirectory() as tmp:
            first, second = Path(tmp) / "first", Path(tmp) / "second"
            miner.write_outputs(mined, candidates, first)
            miner.write_outputs(replay, replay_candidates, second)
            self.assertEqual(sorted(p.name for p in first.iterdir()), [
                "candidates-2026-10-01.yaml", "digest-2026-10-01.md", "mined-2026-10-01.json",
            ])
            for path in first.iterdir():
                self.assertEqual(path.read_bytes(), (second / path.name).read_bytes())
                for pii in ("synthetic.buyer@example.com", "jamie@example.com", "555", "123456789012", "987654321000"):
                    self.assertNotIn(pii, path.read_text())
            self.assertEqual(json.loads((first / "mined-2026-10-01.json").read_text()), mined)
            drafts = yaml.safe_load((first / "candidates-2026-10-01.yaml").read_text())
            self.assertEqual(drafts["candidates"], candidates)
            self.assertIn("synthetic fixture", drafts["note"])
        digest = miner.digest(mined)
        self.assertIn("5 published sessions in the last 24 h, 11 turns, 10 flagged, 5 new candidate cases.", digest)
        self.assertIn("Source: fixture. Reference time: 2026-10-01T12:00:00+00:00.", digest)
        self.assertIn("verify_customer: Cannot verify <email>; call <number>; reference <number>.", digest)
        for flag, count in EXPECTED_FLAGS.items():
            self.assertIn(f"| {flag} | {count} |", digest)
            self.assertIn(f"## {flag} ({count})", digest)


class SourceAndCliTests(unittest.TestCase):
    def test_live_projection_uses_same_four_stdm_tables(self):
        fixture = FixtureSource(FIXTURE).load()
        source = DataCloudSource("example-org")
        with patch.object(source, "sql", side_effect=[getattr(fixture, key) for key in TABLES]) as sql:
            self.assertEqual(source.load(), fixture)
        for call, (table, fields) in zip(sql.call_args_list, TABLES.values()):
            query = call.args[0]
            self.assertIn(f'FROM "{table}"', query)
            for alias, column in fields.items():
                self.assertIn(f'"{column}" AS {alias}', query)

    def test_live_cli_contract_pagination_and_temporary_body_cleanup(self):
        bodies = []

        def run(command, **kwargs):
            self.assertEqual(command[:4], ["sf", "api", "request", "rest"])
            self.assertEqual(command[-2:], ["--target-org", "example-org"])
            self.assertEqual(kwargs, {"capture_output": True, "text": True})
            if "--body" in command:
                self.assertEqual(command[4], "/services/data/v67.0/ssot/query-sql")
                self.assertIn("POST", command)
                body = Path(command[command.index("--body") + 1][1:])
                self.assertEqual(json.loads(body.read_text()), {"sql": "SELECT sample"})
                bodies.append(body)
                payload = {"metadata": [{"name": "id"}, {"name": "started"}], "data": [["one", "NOT_SET"]],
                           "status": {"rowCount": 3, "queryId": "synthetic-query"}}
            else:
                self.assertEqual(command[4], "/services/data/v67.0/ssot/query-sql/synthetic-query/rows?offset=1&rowLimit=5000")
                payload = {"data": [["two", None], ["three", "2026-10-01T09:00:00Z"]]}
            return Mock(returncode=0, stdout="CLI notice\n" + json.dumps(payload))

        with patch("scripts.trace_sources.subprocess.run", side_effect=run) as rest:
            self.assertEqual(DataCloudSource("example-org").sql("SELECT sample"), [
                {"id": "one", "started": "NOT_SET"}, {"id": "two", "started": None},
                {"id": "three", "started": "2026-10-01T09:00:00Z"},
            ])
            self.assertEqual(rest.call_count, 2)
        self.assertTrue(bodies)
        self.assertTrue(all(not body.exists() for body in bodies))

    def test_live_failures_are_explicit_and_clean_up_request_body(self):
        source = DataCloudSource("example-org")
        bodies = []

        def fail(*args):
            bodies.append(Path(args[-1][1:]))
            raise ValueError("test failure")

        with patch.object(source, "_rest", side_effect=fail), self.assertRaisesRegex(ValueError, "test failure"):
            source.sql("SELECT sample")
        self.assertFalse(bodies[0].exists())
        with patch("scripts.trace_sources.subprocess.run", return_value=Mock(returncode=1)), \
                self.assertRaisesRegex(ValueError, "exit 1"):
            source.sql("SELECT sample")
        with patch("scripts.trace_sources.subprocess.run", return_value=Mock(returncode=0, stdout="not JSON")), \
                self.assertRaisesRegex(ValueError, "invalid JSON"):
            source.sql("SELECT sample")
        with patch.object(source, "_rest", return_value={"error": "failed"}), \
                self.assertRaisesRegex(ValueError, "missing metadata"):
            source.sql("SELECT sample")
        first = {"metadata": [{"name": "id"}], "data": [["one"]], "status": {"rowCount": 2, "queryId": "q"}}
        with patch.object(source, "_rest", side_effect=[first, {"data": []}]), \
                self.assertRaisesRegex(ValueError, "1/2 rows"):
            source.sql("SELECT sample")

    def test_fixture_validation_never_falls_back_to_salesforce(self):
        valid = json.loads(FIXTURE.read_text())
        cases = [[], {**valid, "as_of": "bad"}, {**valid, "as_of": "2026-10-01T12:00:00"},
                 {**valid, "ssot__AiAgentSession__dlm": None},
                 {**valid, "ssot__AiAgentSession__dlm": [{}]}]
        with tempfile.TemporaryDirectory() as tmp, patch("scripts.trace_sources.subprocess.run") as rest:
            path = Path(tmp) / "bad.json"
            for blob in cases:
                path.write_text(json.dumps(blob))
                with self.subTest(blob=str(blob)[:50]), self.assertRaises(ValueError):
                    FixtureSource(path)
            path.write_text("{invalid")
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                miner.main(["--fixture", str(path), "--out", str(Path(tmp) / "out")])
            self.assertEqual(error.exception.code, 2)
            self.assertFalse((Path(tmp) / "out").exists())
            rest.assert_not_called()

    def test_cli_rejects_missing_or_ambiguous_source_and_invalid_hours(self):
        for argv in ([], ["org", "--fixture", str(FIXTURE)],
                     *(["--fixture", str(FIXTURE), "--hours", value] for value in ("0", "-1", "nan", "inf"))):
            with self.subTest(argv=argv), patch.object(miner, "DataCloudSource") as live, \
                    redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                miner.main(argv)
            self.assertEqual(error.exception.code, 2)
            live.assert_not_called()

    def test_live_positional_alias_still_selects_live_source(self):
        records = FixtureSource(FIXTURE).load()
        with tempfile.TemporaryDirectory() as tmp, patch.object(miner, "DataCloudSource") as live, \
                patch.object(miner, "datetime", wraps=datetime) as clock, redirect_stdout(io.StringIO()):
            live.return_value.load.return_value = records
            clock.now.return_value = FixtureSource(FIXTURE).as_of
            self.assertEqual(miner.main(["example-org", "--hours", "24", "--env", "all", "--out", tmp]), 0)
            live.assert_called_once_with("example-org")
            output = json.loads((Path(tmp) / "mined-2026-10-01.json").read_text())
            self.assertEqual(output["summary"]["source"], "data_cloud")
            self.assertEqual(output["summary"]["sessions"], 6)

    def test_offline_main_needs_neither_live_source_nor_wall_clock(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(miner, "DataCloudSource", side_effect=AssertionError("offline touched live source")), \
                patch.object(miner, "datetime", wraps=datetime) as clock, \
                patch("scripts.trace_sources.subprocess.run", side_effect=AssertionError("offline invoked subprocess")), \
                redirect_stdout(io.StringIO()):
            clock.now.side_effect = AssertionError("offline read wall clock")
            self.assertEqual(miner.main(["--fixture", str(FIXTURE), "--out", tmp]), 0)

    def test_cli_from_outside_repo_repeats_identical_artifacts_without_sf(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # A trap makes any accidental sf invocation observable, even on a developer machine.
            trap = root / "sf"
            marker = root / "sf-was-called"
            trap.write_text('#!/bin/sh\ntouch "$SF_TEST_MARKER"\nexit 73\n')
            trap.chmod(0o755)
            outputs = []
            for seed in ("1", "2"):
                out = root / seed
                result = subprocess.run(
                    [sys.executable, str(ROOT / "scripts/trace_miner.py"), "--fixture", str(FIXTURE), "--out", str(out)],
                    cwd=root, env={**os.environ, "PATH": str(root) + os.pathsep + os.environ.get("PATH", ""),
                                   "PYTHONHASHSEED": seed, "SF_TEST_MARKER": str(marker)},
                    capture_output=True, text=True, check=True,
                )
                self.assertEqual(json.loads(result.stdout)["candidates"], 5)
                outputs.append({p.name: p.read_bytes() for p in out.iterdir()})
            self.assertFalse(marker.exists())
            self.assertEqual(outputs[0], outputs[1])


if __name__ == "__main__":
    unittest.main()
