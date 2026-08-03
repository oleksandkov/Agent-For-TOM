#!/usr/bin/env python3
"""
User config must survive `TOMAS update`.

install.ps1 deletes $SrcDir wholesale on every upgrade. Anything the CLI
writes into the source tree is therefore destroyed — this happened to
providers.json and to the .env holding every API key set through the menus.
The rule these tests enforce: user state lives in ~/.tomas/, source lives in
the source directory, and the two never mix.

Run: python -m unittest discover -s tests -p "test_*.py"
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent.resolve()
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import agent_cli


class EnvFileTestCase(unittest.TestCase):
    """Redirect the module's paths at a scratch dir for each test."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.tomas_dir = root / ".tomas"
        self.src_dir = self.tomas_dir / "src"
        self.src_dir.mkdir(parents=True)
        self.env_file = self.tomas_dir / ".env"
        self.legacy = self.src_dir / ".env"

        self._saved = {k: getattr(agent_cli, k) for k in
                       ("PROJECT_DIR", "TOMAS_DIR", "ENV_FILE", "_LEGACY_ENV_FILE")}
        agent_cli.PROJECT_DIR = self.src_dir
        agent_cli.TOMAS_DIR = self.tomas_dir
        agent_cli.ENV_FILE = self.env_file
        agent_cli._LEGACY_ENV_FILE = self.legacy
        self._saved_environ = dict(os.environ)

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(agent_cli, k, v)
        os.environ.clear()
        os.environ.update(self._saved_environ)
        self._tmp.cleanup()

    def read(self, path) -> dict:
        if not path.exists():
            return {}
        return dict(
            ln.split("=", 1) for ln in
            path.read_text(encoding="utf-8").splitlines() if "=" in ln
        )


class TestUpdateDotenvWritesDurably(EnvFileTestCase):
    def test_key_is_written_outside_the_source_tree(self):
        agent_cli.update_dotenv("ANTHROPIC_API_KEY", "sk-secret")

        self.assertEqual(self.read(self.env_file)["ANTHROPIC_API_KEY"], "sk-secret")
        self.assertFalse(self.legacy.exists(),
                         "nothing may be written into $SrcDir")

    def test_existing_key_is_replaced_not_duplicated(self):
        agent_cli.update_dotenv("AGENT_MODEL", "model-a")
        agent_cli.update_dotenv("AGENT_MODEL", "model-b")

        body = self.env_file.read_text(encoding="utf-8")
        self.assertEqual(body.count("AGENT_MODEL="), 1)
        self.assertEqual(self.read(self.env_file)["AGENT_MODEL"], "model-b")

    def test_running_process_sees_the_change_immediately(self):
        agent_cli.update_dotenv("AGENT_MODEL", "model-x")
        self.assertEqual(os.environ["AGENT_MODEL"], "model-x")

    def test_shadowing_key_is_dropped_so_the_change_takes_effect(self):
        """A checkout .env is loaded with override=True. Leaving a stale entry
        there would silently revert what the user just set in the menus."""
        self.legacy.write_text("AGENT_MODEL=stale\nOTHER=keep\n", encoding="utf-8")

        agent_cli.update_dotenv("AGENT_MODEL", "fresh")

        self.assertEqual(self.read(self.env_file)["AGENT_MODEL"], "fresh")
        legacy = self.read(self.legacy)
        self.assertNotIn("AGENT_MODEL", legacy, "shadowing entry must be gone")
        self.assertEqual(legacy["OTHER"], "keep", "unrelated keys untouched")


class TestSrcEnvMigration(EnvFileTestCase):
    def test_deployed_src_env_is_rescued(self):
        self.legacy.write_text("ANTHROPIC_API_KEY=sk-old\nAGENT_MODEL=m1\n",
                               encoding="utf-8")

        agent_cli._migrate_src_env()

        rescued = self.read(self.env_file)
        self.assertEqual(rescued["ANTHROPIC_API_KEY"], "sk-old")
        self.assertEqual(rescued["AGENT_MODEL"], "m1")
        self.assertFalse(self.legacy.exists(), "old file must be moved aside")
        self.assertTrue((self.src_dir / ".env.migrated").exists(),
                        "moved-aside file keeps a sane name")

    def test_existing_durable_values_win(self):
        self.env_file.write_text("AGENT_MODEL=keep-me\n", encoding="utf-8")
        self.legacy.write_text("AGENT_MODEL=older\nOPENAI_API_KEY=sk-x\n",
                               encoding="utf-8")

        agent_cli._migrate_src_env()

        merged = self.read(self.env_file)
        self.assertEqual(merged["AGENT_MODEL"], "keep-me")
        self.assertEqual(merged["OPENAI_API_KEY"], "sk-x")

    def test_dev_checkout_env_is_left_alone(self):
        """A dev checkout's .env is the developer's own file, and an update
        never deletes it — migrating it would clobber their global config."""
        checkout = Path(self._tmp.name) / "checkout"
        checkout.mkdir()
        dev_env = checkout / ".env"
        dev_env.write_text("AGENT_MODEL=dev-model\n", encoding="utf-8")
        agent_cli.PROJECT_DIR = checkout
        agent_cli._LEGACY_ENV_FILE = dev_env

        agent_cli._migrate_src_env()

        self.assertTrue(dev_env.exists())
        self.assertFalse(self.env_file.exists())

    def test_migration_is_idempotent(self):
        self.legacy.write_text("AGENT_MODEL=m1\n", encoding="utf-8")
        agent_cli._migrate_src_env()
        agent_cli._migrate_src_env()
        self.assertEqual(self.read(self.env_file)["AGENT_MODEL"], "m1")


class TestProvidersConfigLocation(unittest.TestCase):
    def test_providers_config_lives_outside_the_source_tree(self):
        self.assertNotIn(
            agent_cli.PROJECT_DIR.resolve(),
            agent_cli.PROVIDERS_CONFIG_PATH.resolve().parents,
            "providers.json must not sit in the source tree",
        )


if __name__ == "__main__":
    unittest.main()
