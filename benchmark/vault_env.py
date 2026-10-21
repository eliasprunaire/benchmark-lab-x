"""Injecte les clés du bench dans un processus fils.

Le shell parent garde ses variables. Une variable héritée du même nom est retirée
avant l'écriture, parce que `uv --env-file` ne remplace pas une variable déjà posée.
"""
import os
from pathlib import Path
import subprocess
import sys

VAULT_PATH = "secret/saas/labx-benchmark/benchmark-lab-x/api"
FIELDS = {
    "OPENROUTER_API_KEY": "openrouter_benchmark",
    "JEV_API_KEY": "jev_benchmark",
    "DEEPSEEK_API_KEY": "deepseek_benchmark",
    "ANTHROPIC_API_KEY": "anthropic_benchmark",
    "ZAI_API_KEY": "zai_benchmark",
    "OPENAI_API_KEY": "openai_benchmark",
    "MOONSHOT_API_KEY": "moonshot_benchmark",
    "DASHSCOPE_API_KEY": "dashscope_benchmark",
    "TENCENT_TOKENHUB_API_KEY": "tencent_tokenhub_benchmark",
    "GROK_API_KEY": "grok_benchmark",
    "MINIMAX_API_KEY": "minimax_benchmark",
    "MISTRAL_API_KEY": "mistral_benchmark",
}


def parse_public_env(text):
    """Retourne les affectations du fichier qui ne sont pas des clés du bench."""
    public = {}
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "" or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        name = name.strip()
        if name in FIELDS:
            continue
        public[name] = value.strip().strip("\"'")
    return public


def load_secrets(read):
    """Lit chaque champ Vault. Un champ vide ou refusé lève RuntimeError(champ)."""
    loaded = {}
    for name, field in FIELDS.items():
        value = read(field)
        if not isinstance(value, str) or value == "":
            raise RuntimeError(field)
        loaded[name] = value
    return loaded


def child_environment(parent, secrets, public=None):
    """Copie le parent, retire les noms de clés, puis pose les URL et les secrets."""
    child = dict(parent)
    for name in FIELDS:
        child.pop(name, None)
    if public:
        for name, value in public.items():
            if name not in FIELDS:
                child[name] = value
    child.update(secrets)
    return child


def vault_read(field):
    binary = os.environ.get("VAULT_BIN", "/opt/homebrew/bin/vault")
    completed = subprocess.run(
        [binary, "kv", "get", f"-field={field}", VAULT_PATH],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(field)
    value = completed.stdout.strip()
    if value == "":
        raise RuntimeError(field)
    return value


def main(argv):
    if argv[:1] == ["--"]:
        argv = argv[1:]
    if not argv:
        raise SystemExit("commande absente")
    secrets = load_secrets(vault_read)
    env_path = Path(os.environ.get("BENCHMARK_ENV_FILE", Path.cwd() / ".env"))
    public = parse_public_env(env_path.read_text()) if env_path.is_file() else {}
    os.execvpe(argv[0], argv, child_environment(os.environ, secrets, public))


if __name__ == "__main__":
    main(sys.argv[1:])
