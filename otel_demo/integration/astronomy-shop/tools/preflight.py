"""Check live-stack readiness without mutating anything.

Default checks are local. `--execute` additionally runs the Compose config
validation; `--probe` performs read-only HTTP checks against a running stack.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence

# Allow `python3 tools/preflight.py`.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.build_corpus import PINNED_COMMIT, WORKSPACE, build_payload

VENDOR = WORKSPACE / "vendor" / "opentelemetry-demo"
INTEGRATION = WORKSPACE / "integration" / "astronomy-shop"
DOCS = WORKSPACE / "docs"

REQUIRED_FILES = (
    "vendor/opentelemetry-demo/compose.yaml",
    "vendor/opentelemetry-demo/compose.full.yaml",
    "vendor/opentelemetry-demo/compose.observability.yaml",
    "vendor/opentelemetry-demo/compose.extras.yaml",
    "integration/astronomy-shop/compose.overlay.yaml",
    "integration/astronomy-shop/otelcol-config-extras.yml",
    "integration/astronomy-shop/grafana/provisioning/datasources/lgtm.yaml",
    "integration/astronomy-shop/grafana/provisioning/alerting/astronomy-shop-alerts.yaml",
    "integration/astronomy-shop/grafana/dashboards/astronomy-shop-investigation.json",
    "integration/astronomy-shop/adapter/__main__.py",
    "integration/astronomy-shop/docs-corpus/corpus.jsonl",
    "integration/astronomy-shop/docs-corpus/manifest.json",
    "integration/astronomy-shop/scenarios/catalog.json",
    "docs/HANDOFF.md",
    "docs/live-validation.md",
)

COMPOSE_FILES = (
    "compose.yaml",
    "compose.full.yaml",
    "compose.observability.yaml",
    "compose.extras.yaml",
    "../../integration/astronomy-shop/compose.overlay.yaml",
)

PROBES = (
    ("store", "http://localhost:8080/"),
    ("flagd-read", "http://localhost:8080/feature/api/read"),
    ("lgtm-grafana", "http://127.0.0.1:3001/api/health"),
    ("jaeger-ui", "http://localhost:8080/jaeger/ui"),
    # The module mounts its datasources, dashboard, and alerts into the
    # upstream Grafana, so that one is what the probes have to reach.
    ("grafana-upstream", "http://localhost:8080/grafana/api/health"),
    ("prometheus", "http://localhost:9090/-/healthy"),
    # Tempo and Loki come from the LGTM overlay, not the upstream stack.
    ("lgtm-tempo", "http://127.0.0.1:3200/ready"),
    ("lgtm-loki", "http://127.0.0.1:3100/ready"),
)

Run = Callable[..., Any]


@dataclass(frozen=True)
class Check:
    name: str
    status: str  # pass | warn | fail | skip
    detail: str

    @property
    def ok(self) -> bool:
        return self.status in {"pass", "warn", "skip"}


def check_files(root: Path = WORKSPACE) -> list[Check]:
    missing = [relative for relative in REQUIRED_FILES if not (root / relative).is_file()]
    if missing:
        return [Check("required files", "fail", "missing: " + ", ".join(missing))]
    return [Check("required files", "pass", f"{len(REQUIRED_FILES)} present")]


def check_vendor(run: Run = subprocess.run) -> list[Check]:
    if not (VENDOR / ".git").exists():
        return _check_vendored_snapshot(run)
    result = run(
        ["git", "-C", str(VENDOR), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        return [Check("vendor commit", "warn", "git rev-parse failed")]
    head = result.stdout.strip()
    if head != PINNED_COMMIT:
        return [Check("vendor commit", "fail", f"HEAD {head[:12]} != pinned {PINNED_COMMIT[:12]}")]
    status = run(
        ["git", "-C", str(VENDOR), "status", "--porcelain"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if status.returncode == 0 and status.stdout.strip():
        return [Check("vendor commit", "warn", "pinned, but checkout is dirty")]
    return [Check("vendor commit", "pass", PINNED_COMMIT[:12])]


def _check_vendored_snapshot(run: Run = subprocess.run) -> list[Check]:
    """Verify the vendored copy when it is not a git checkout of its own.

    The vendored tree is committed into this repository, so the pin is recorded
    in `vendor/opentelemetry-demo/.pinned-commit` and the tree is compared
    against this repository's committed copy.
    """
    pin_file = VENDOR / ".pinned-commit"
    if not pin_file.is_file():
        return [Check("vendor commit", "warn", "no pin record; cannot verify pin")]
    recorded = pin_file.read_text(encoding="utf-8").strip()
    if recorded != PINNED_COMMIT:
        return [Check("vendor commit", "fail", f"recorded {recorded[:12]} != pinned {PINNED_COMMIT[:12]}")]
    status = run(
        ["git", "-C", str(WORKSPACE), "status", "--porcelain", "--", "vendor/opentelemetry-demo"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if status.returncode != 0:
        return [Check("vendor commit", "warn", "vendored snapshot recorded; cannot compare with git")]
    if status.stdout.strip():
        return [Check("vendor commit", "warn", "pinned, but vendored snapshot differs from the committed copy")]
    return [Check("vendor commit", "pass", PINNED_COMMIT[:12])]


def check_corpus() -> list[Check]:
    corpus_path = INTEGRATION / "docs-corpus" / "corpus.jsonl"
    manifest_path = INTEGRATION / "docs-corpus" / "manifest.json"
    if not corpus_path.is_file() or not manifest_path.is_file():
        return [Check("corpus", "fail", "generated corpus missing")]
    corpus, manifest, _ = build_payload()
    if corpus_path.read_text(encoding="utf-8") != corpus:
        return [Check("corpus", "fail", "corpus is stale; run tools/build_corpus.py")]
    if manifest_path.read_text(encoding="utf-8") != manifest:
        return [Check("corpus", "fail", "manifest is stale; run tools/build_corpus.py")]
    return [Check("corpus", "pass", "up to date")]


def check_docker(run: Run = subprocess.run, which: Callable[[str], Optional[str]] = shutil.which) -> list[Check]:
    if not which("docker"):
        return [Check("docker", "fail", "docker executable not found")]
    result = run(
        ["docker", "compose", "version"], capture_output=True, text=True, timeout=15
    )
    if result.returncode != 0:
        return [Check("docker", "fail", "docker compose is not available")]
    return [Check("docker", "pass", result.stdout.strip().splitlines()[0][:80])]


def compose_command() -> list[str]:
    command = ["docker", "compose", "config"]
    for relative in COMPOSE_FILES:
        command.extend(["-f", relative])
    return command


def check_compose_config(run: Run = subprocess.run) -> list[Check]:
    try:
        result = run(
            compose_command(),
            cwd=str(VENDOR),
            capture_output=True,
            text=True,
            timeout=90,
        )
    except OSError as exc:
        return [Check("compose config", "fail", f"unable to run docker: {exc.strerror or exc}")]
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        return [Check("compose config", "fail", detail[-1][:200] if detail else "exit non-zero")]
    return [Check("compose config", "pass", "resolved successfully")]


def _probe(url: str, timeout: float = 3.0) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            code = response.status
            body = response.read(200)
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, str(getattr(exc, "reason", exc))[:120]
    if code >= 400:
        return False, f"HTTP {code}"
    if url.endswith("/feature/api/read") and b'"flags"' not in body:
        return False, "response has no flags map"
    return True, f"HTTP {code}"


def probe_checks(timeout: float = 3.0) -> list[Check]:
    checks = []
    for name, url in PROBES:
        ok, detail = _probe(url, timeout)
        checks.append(Check(f"probe {name}", "pass" if ok else "fail", detail))
    return checks


def summarize(checks: Iterable[Check]) -> dict:
    counts = {"pass": 0, "warn": 0, "fail": 0, "skip": 0}
    for check in checks:
        counts[check.status] = counts.get(check.status, 0) + 1
    return counts


def render(checks: list[Check], *, as_json: bool = False) -> str:
    if as_json:
        return json.dumps(
            {"checks": [asdict(check) for check in checks], "summary": summarize(checks)},
            indent=2,
        )
    lines = [f"{check.status.upper():4}  {check.name:22} {check.detail}" for check in checks]
    counts = summarize(checks)
    lines.append(
        f"\n{counts['pass']} pass, {counts['warn']} warn, {counts['fail']} fail, "
        f"{counts['skip']} skip"
    )
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Check live-stack readiness (read-only).")
    parser.add_argument("--local-only", action="store_true", help="skip Docker and network checks")
    parser.add_argument("--execute", action="store_true", help="run docker compose config")
    parser.add_argument("--probe", action="store_true", help="probe a running stack over HTTP")
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    checks: list[Check] = []
    checks += check_files()
    checks += check_vendor()
    checks += check_corpus()

    if args.local_only:
        checks.append(Check("docker", "skip", "--local-only"))
        checks.append(Check("compose config", "skip", "--local-only"))
    else:
        checks += check_docker()
        if args.execute:
            checks += check_compose_config()
        else:
            checks.append(Check("compose config", "skip", "use --execute"))
        if args.probe:
            checks += probe_checks(args.timeout)
        else:
            checks.append(Check("probes", "skip", "use --probe"))

    print(render(checks, as_json=args.json))
    return 0 if all(check.ok for check in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
