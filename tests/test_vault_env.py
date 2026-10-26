"""Injection des clés du bench dans le processus fils seulement"""
import unittest

from benchmark.vault_env import FIELDS, child_environment, load_secrets, parse_public_env


class VaultEnvTests(unittest.TestCase):
    def test_child_replaces_inherited_openrouter_and_keeps_parent(self):
        parent = {
            "OPENROUTER_API_KEY": "agents-key",
            "DASHSCOPE_BASE_URL": "https://dash.example",
            "PATH": "/bin",
        }
        secrets = {"OPENROUTER_API_KEY": "bench-key", "DEEPSEEK_API_KEY": "deepseek-key"}

        child = child_environment(parent, secrets)

        self.assertEqual(child["OPENROUTER_API_KEY"], "bench-key")
        self.assertEqual(child["DEEPSEEK_API_KEY"], "deepseek-key")
        self.assertEqual(child["DASHSCOPE_BASE_URL"], "https://dash.example")
        self.assertEqual(parent["OPENROUTER_API_KEY"], "agents-key")

    def test_public_env_ignores_secret_names(self):
        text = "\n".join([
            "# commentaire",
            "OPENROUTER_API_KEY=from-file",
            "DASHSCOPE_BASE_URL=https://dash.example",
            "TENCENT_TOKENHUB_BASE_URL=https://token.example",
            "",
        ])

        public = parse_public_env(text)

        self.assertEqual(public, {
            "DASHSCOPE_BASE_URL": "https://dash.example",
            "TENCENT_TOKENHUB_BASE_URL": "https://token.example",
        })

    def test_load_secrets_reads_each_field_and_rejects_empty(self):
        seen = []

        def read(field):
            seen.append(field)
            if field == "mistral_benchmark":
                return ""
            return f"value-{field}"

        with self.assertRaises(RuntimeError) as raised:
            load_secrets(read)

        self.assertEqual(raised.exception.args, ("mistral_benchmark",))
        self.assertEqual(seen, list(FIELDS.values()))

    def test_child_applies_public_lines_then_vault_secrets(self):
        parent = {"OPENROUTER_API_KEY": "agents-key"}
        public = parse_public_env("OPENROUTER_API_KEY=from-file\nDASHSCOPE_BASE_URL=https://dash.example\n")
        secrets = {"OPENROUTER_API_KEY": "bench-key"}

        child = child_environment(parent, secrets, public)

        self.assertEqual(child["OPENROUTER_API_KEY"], "bench-key")
        self.assertEqual(child["DASHSCOPE_BASE_URL"], "https://dash.example")
        self.assertEqual(parent["OPENROUTER_API_KEY"], "agents-key")


if __name__ == "__main__":
    unittest.main()
