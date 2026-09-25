#!/usr/bin/env python3
"""Direct file-I/O vault client (replaces the old Obsidian Local REST API client).

A vault is just a folder of Markdown files. Obsidian discovers external writes
through its own filesystem watcher, so nothing here needs an API key, a port,
HTTPS, or a certificate. Concurrency is guarded with a cross-process
`filelock` on the note path instead of the plugin's `If-Match` content hash.

Public surface is unchanged from the REST version:
    read_note, read_frontmatter, patch_section, append_run_log,
    write_incident, search
plus the writer helpers the swarm nodes need:
    ensure_vault, write_lead, patch_frontmatter, resolve_note, list_notes

Self-test (concurrency):  python3 orchestrator/obsidian_client.py --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence

try:  # pragma: no cover - exercised by the self-test if absent
    from filelock import FileLock, Timeout as FileLockTimeout

    HAVE_FILELOCK = True
except Exception:  # pragma: no cover
    HAVE_FILELOCK = False
    FileLock = None  # type: ignore
    FileLockTimeout = TimeoutError  # type: ignore

try:
    import yaml

    HAVE_YAML = True
except Exception:  # pragma: no cover
    HAVE_YAML = False
    yaml = None  # type: ignore

# ---------------------------------------------------------------------------
# Frontmatter parsing (small dedicated parser; python-frontmatter not required)
# ---------------------------------------------------------------------------
_MINIMAL_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---[ \t]*\r?\n?", re.DOTALL)


def _scalar(text: str) -> Any:
    """Parse one YAML-ish scalar without needing a full YAML stack."""
    t = text.strip()
    if t == "" or t in {"~", "null", "Null", "NULL"}:
        return None
    low = t.lower()
    if low in {"true", "yes"}:
        return True
    if low in {"false", "no"}:
        return False
    if (t.startswith("'") and t.endswith("'")) or (t.startswith('"') and t.endswith('"')):
        return t[1:-1]
    if re.fullmatch(r"-?\d+", t):
        return int(t)
    if re.fullmatch(r"-?\d+\.\d+", t):
        return float(t)
    if t.startswith("[") or t.startswith("{"):
        try:
            return json.loads(t.replace("'", '"'))
        except Exception:
            return t
    return t


def _parse_scalars_fallback(block: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for raw in block.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith((" ", "\t", "-")):
            continue  # nested structures are not used by the lead schema
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        out[key.strip()] = _scalar(val)
    return out


def parse_frontmatter(text: str) -> tuple[Dict[str, Any], str, bool]:
    """Return (frontmatter, body, had_frontmatter)."""
    m = _MINIMAL_FRONTMATTER.match(text)
    if not m:
        return {}, text, False
    block, body = m.group(1), text[m.end():]
    if HAVE_YAML:
        try:
            data = yaml.safe_load(block)
            if data is None:
                data = {}
            if not isinstance(data, dict):
                raise ValueError("frontmatter is not a mapping")
            return {str(k): v for k, v in data.items()}, body, True
        except Exception:
            pass
    return _parse_scalars_fallback(block), body, True


def dump_frontmatter(data: Mapping[str, Any]) -> str:
    if HAVE_YAML:
        return yaml.safe_dump(dict(data), sort_keys=False, allow_unicode=True, default_flow_style=False).rstrip("\n")
    lines = []
    for k, v in data.items():
        if v is None:
            v = "null"
        elif isinstance(v, bool):
            v = "true" if v else "false"
        lines.append(f"{k}: {v}")
    return "\n".join(lines)


def build_note(frontmatter: Mapping[str, Any], body: str) -> str:
    return f"---\n{dump_frontmatter(frontmatter)}\n---\n{body.lstrip(chr(10))}"


# ---------------------------------------------------------------------------
# Vault / note resolution
# ---------------------------------------------------------------------------
def _vault(vault: str | os.PathLike[str]) -> Path:
    return Path(vault)


def ensure_vault(vault: str | os.PathLike[str]) -> Path:
    root = _vault(vault)
    for sub in ("Leads", "_runs", "_incidents", "Evidence"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def leads_dir(vault: str | os.PathLike[str]) -> Path:
    return _vault(vault) / "Leads"


def resolve_note(vault: str | os.PathLike[str], lead_or_path: Any) -> Path:
    """Accept a lead_id, a note filename, a Path, or a frontmatter mapping."""
    if isinstance(lead_or_path, Mapping):
        lead_or_path = lead_or_path.get("lead_id") or lead_or_path.get("email") or ""
    p = Path(str(lead_or_path))
    if p.is_absolute() and p.suffix == ".md":
        return p
    name = str(lead_or_path)
    if name.endswith(".md"):
        return leads_dir(vault) / name
    return leads_dir(vault) / f"{name}.md"


def lock_for(path: Path, timeout: float = 30.0):
    """Cross-process lock keyed on the note's path."""
    lock_path = Path(str(path) + ".lock")
    if HAVE_FILELOCK:
        return FileLock(str(lock_path), timeout=timeout)
    raise RuntimeError("the 'filelock' package is required for safe vault writes")


def list_notes(vault: str | os.PathLike[str], subdir: str = "Leads") -> List[Path]:
    root = _vault(vault) / subdir
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*.md") if not p.name.endswith(".lock"))


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
def read_note(vault: str | os.PathLike[str], lead: Any) -> str:
    """Full note text (frontmatter included)."""
    return resolve_note(vault, lead).read_text(encoding="utf-8")


def read_body(vault: str | os.PathLike[str], lead: Any) -> str:
    _, body, _ = parse_frontmatter(read_note(vault, lead))
    return body


def read_frontmatter(vault: str | os.PathLike[str], lead: Any) -> Dict[str, Any]:
    fm, _, _ = parse_frontmatter(read_note(vault, lead))
    return fm


# ---------------------------------------------------------------------------
# Writes (all lock-guarded)
# ---------------------------------------------------------------------------
def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write_note(vault: str | os.PathLike[str], lead: Any, frontmatter: Mapping[str, Any], body: str) -> Path:
    path = resolve_note(vault, lead)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = build_note(frontmatter, body)
    with lock_for(path):
        _atomic_write(path, text if text.endswith("\n") else text + "\n")
    return path


def patch_frontmatter(vault: str | os.PathLike[str], lead: Any, updates: Mapping[str, Any]) -> List[str]:
    """Merge `updates` into the note's frontmatter. Returns changed keys."""
    path = resolve_note(vault, lead)
    with lock_for(path):
        text = path.read_text(encoding="utf-8")
        fm, body, had = parse_frontmatter(text)
        if not had:
            raise ValueError(f"{path} has no YAML frontmatter block")
        changed: List[str] = []
        for k, v in updates.items():
            if fm.get(k) != v:
                changed.append(k)
            fm[k] = v
        _atomic_write(path, build_note(fm, body))
    return changed


def assert_not_terminal_free(updates: Mapping[str, Any]) -> None:
    """Guard used by reporter.py to make sure only it writes terminal fields."""
    try:
        from orchestrator.policy.reporter import TERMINAL_FRONTMATTER
    except Exception:
        return
    bad = sorted(k for k in updates if k in TERMINAL_FRONTMATTER)
    if bad:
        raise PermissionError(f"terminal frontmatter fields are reporter-owned: {bad}")


def _heading_span(text: str, heading: str) -> tuple[int, int]:
    """Return (start_offset_of_heading, start_offset_of_next_heading_or_EOF)."""
    pattern = re.compile(r"^[ \t]*" + re.escape(heading) + r"[ \t]*$", re.MULTILINE)
    m = pattern.search(text)
    if not m:
        raise KeyError(f"heading not found: {heading!r}")
    start = m.start()
    next_m = re.compile(r"^#{1,6}[ \t]+\S", re.MULTILINE).search(text, m.end())
    end = next_m.start() if next_m else len(text)
    return start, end


def patch_section(
    vault: str | os.PathLike[str],
    lead: Any,
    heading: str,
    content: str,
    *,
    position: str = "before_next_heading",
) -> Path:
    """Insert `content` into the section starting at `heading`.

    position='before_next_heading' (default) inserts immediately before the next
    heading — the multi-line body form. position='end_of_section' appends as the
    last line of the section, which is what reporter.append_run_log uses so
    consecutive run-log lines stay in chronological order.
    """
    path = resolve_note(vault, lead)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not content.endswith("\n"):
        content = content + "\n"
    with lock_for(path):
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        if not text:
            text = build_note({}, f"\n# {path.stem}\n\n{heading}\n")
        start, end = _heading_span(text, heading)
        if position == "before_next_heading":
            insert_at = end
            # keep one blank line of separation before the next heading
            if insert_at < len(text):
                prefix = text[:insert_at].rstrip("\n")
                suffix = text[insert_at:]
                new_text = f"{prefix}\n\n{content.rstrip(chr(10))}\n\n{suffix}"
            else:
                new_text = text.rstrip("\n") + "\n\n" + content
        elif position == "end_of_section":
            new_text = text[:end].rstrip("\n") + "\n" + content + text[end:]
        else:
            raise ValueError(f"unknown position: {position}")
        _atomic_write(path, new_text)
    return path


def append_run_log(vault: str | os.PathLike[str], lead: Any, line: str) -> Path:
    """Append one run-log line to the note's '## Run log' section."""
    if not line.startswith("-"):
        line = f"- {line}"
    return patch_section(vault, lead, "## Run log", line, position="end_of_section")


def write_incident(
    vault: str | os.PathLike[str],
    title: str,
    body: str,
    *,
    severity: str = "warning",
    run_id: str = "",
) -> Path:
    root = _vault(vault)
    (root / "_incidents").mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "incident"
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    path = root / "_incidents" / f"{stamp}-{slug}.md"
    fm = {
        "type": "incident",
        "title": title,
        "severity": severity,
        "run_id": run_id or None,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    path.write_text(build_note(fm, f"\n# {title}\n\n{body}\n"), encoding="utf-8")
    return path


def write_lead(vault: str | os.PathLike[str], frontmatter: Mapping[str, Any], body: str | None = None) -> Path:
    """Write a lead note in the schema's key order (Section A then Section B)."""
    lead_id = str(frontmatter.get("lead_id") or "")
    if not lead_id:
        raise ValueError("lead_id is required to write a lead note")
    ordered = order_like_schema(frontmatter)
    if body is None:
        body = render_lead_body(ordered)
    ensure_vault(vault)
    return write_note(vault, lead_id, ordered, body)


# ---------------------------------------------------------------------------
# Schema ordering + default body
# ---------------------------------------------------------------------------
SCHEMA_ORDER: tuple[str, ...] = (
    "lead_id", "name", "title", "company", "domain", "email", "country",
    "industry", "pain_point", "angle", "source", "source_row", "unsubscribed",
    "status", "ingested_at",
    # Section B (terminal, reporter-owned)
    "run_id", "last_contacted_at", "send_count", "compliance_verdict",
    "delivery_status", "final_status",
)

TERMINAL_KEYS: tuple[str, ...] = (
    "run_id", "last_contacted_at", "send_count", "compliance_verdict",
    "delivery_status", "final_status",
)


def order_like_schema(fm: Mapping[str, Any]) -> Dict[str, Any]:
    defaults: Dict[str, Any] = {
        "lead_id": "", "name": "", "title": "", "company": "", "domain": "",
        "email": "", "country": "", "industry": "", "pain_point": "", "angle": "",
        "source": "", "source_row": 0, "unsubscribed": False, "status": "new",
        "ingested_at": "", "run_id": None, "last_contacted_at": None,
        "send_count": 0, "compliance_verdict": None, "delivery_status": None,
        "final_status": None,
    }
    merged = dict(defaults)
    for k, v in fm.items():
        merged[k] = v
    ordered = {k: merged[k] for k in SCHEMA_ORDER if k in merged}
    for k in merged:
        if k not in ordered:
            ordered[k] = merged[k]
    return ordered


def render_lead_body(fm: Mapping[str, Any]) -> str:
    return (
        f"\n# {fm.get('name') or 'Unknown'} — {fm.get('company') or 'Unknown'}\n\n"
        f"- **Role:** {fm.get('title') or ''}\n"
        f"- **Industry:** {fm.get('industry') or ''}\n"
        f"- **Country:** {fm.get('country') or ''}\n"
        f"- **Pain point:** {fm.get('pain_point') or ''}\n"
        f"- **Angle:** {fm.get('angle') or ''}\n\n"
        "## Evidence\n"
        "<!-- copywriter appends the evidence graph walk here -->\n\n"
        "## Run log\n"
        "<!-- sender/reporter append run-log lines here -->\n"
    )


# ---------------------------------------------------------------------------
# Search: plain recursive scan, no index
# ---------------------------------------------------------------------------
def search(
    vault: str | os.PathLike[str],
    query: str,
    *,
    fields: Sequence[str] | None = None,
    subdir: str = "Leads",
) -> List[Dict[str, Any]]:
    """Recursively scan the vault for a text match in frontmatter or body."""
    q = (query or "").strip().lower()
    hits: List[Dict[str, Any]] = []
    for path in list_notes(vault, subdir):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        fm, body, _ = parse_frontmatter(text)
        matched_in: List[str] = []
        for k, v in fm.items():
            if fields and k not in fields:
                continue
            if q and q in str(v).lower():
                matched_in.append(f"frontmatter:{k}")
        if not fields and q and q in body.lower():
            matched_in.append("body")
        if matched_in:
            hits.append({"path": str(path), "lead_id": fm.get("lead_id") or path.stem, "matched_in": matched_in})
    return hits


# ---------------------------------------------------------------------------
# Self-test (the concurrency test demanded by BUILD Task 0.5)
# ---------------------------------------------------------------------------
def _selftest() -> int:
    import multiprocessing as mp

    results: List[str] = []
    failures: List[str] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        (results if ok else failures).append(f"{label}{(' — ' + detail) if detail else ''}")

    check("filelock importable (required for the Task 0.5 guard)", HAVE_FILELOCK,
          "pip install filelock")
    if not HAVE_FILELOCK:
        for r in results:
            print(f"PASS  {r}")
        for f in failures:
            print(f"FAIL  {f}")
        return 1

    with tempfile.TemporaryDirectory(prefix="vault-selftest-") as tmp:
        vault = Path(tmp) / "vault"
        ensure_vault(vault)
        lead = {
            "lead_id": "ld-concurrency", "name": "Grace Hopper", "title": "Rear Admiral",
            "company": "USN", "domain": "navy.example", "email": "grace@navy.example",
            "country": "US", "industry": "defense", "pain_point": "manual reports",
            "angle": "automate", "source": "selftest", "source_row": 1, "unsubscribed": False,
            "status": "new", "ingested_at": "2026-01-01T00:00:00Z",
        }
        note = write_lead(vault, lead)
        check("lead note written from schema order", note.exists(), note.name)

        fm = read_frontmatter(vault, "ld-concurrency")
        check("read_frontmatter parses the block between --- markers",
              fm.get("lead_id") == "ld-concurrency" and fm.get("send_count") == 0 and fm.get("run_id") is None,
              f"lead_id={fm.get('lead_id')} send_count={fm.get('send_count')} run_id={fm.get('run_id')}")
        check("no REST/API-key/port surface remains",
              not any(hasattr(_mod, _n) for _mod in (sys.modules[__name__],) for _n in ("API_KEY", "PORT", "BASE_URL")))
        src = Path(__file__).read_text(encoding="utf-8")
        check("source has no obsidian REST references",
              "Local REST API" not in src.split("Self-test (concurrency)")[0].replace(
                  "replaces the old Obsidian Local REST API client", ""),
              "only the historical note in the docstring mentions REST")

        # --- the real self-test: two near-simultaneous patch_section calls ---
        def _worker(tag: str) -> None:
            for i in range(3):
                patch_section(vault, "ld-concurrency", "## Evidence", f"- {tag} write {i}")

        procs = [mp.Process(target=_worker, args=(tag,)) for tag in ("procA", "procB")]
        for p in procs:
            p.start()
        for p in procs:
            p.join(60)
        check("both worker processes exited 0", all(p.exitcode == 0 for p in procs),
              str([p.exitcode for p in procs]))

        text = read_note(vault, "ld-concurrency")
        for tag in ("procA", "procB"):
            for i in range(3):
                line = f"- {tag} write {i}"
                check(f"final file contains {line!r}", line in text)
        check("frontmatter survived the concurrent section writes",
              read_frontmatter(vault, "ld-concurrency").get("lead_id") == "ld-concurrency",
              "frontmatter intact")
        check("no torn/partial heading lines",
              text.count("## Evidence") == 1 and text.count("## Run log") == 1,
              f"Evidence={text.count('## Evidence')} Run log={text.count('## Run log')}")

        # thread-level race on the same note
        import threading

        def _t(tag: str) -> None:
            for i in range(5):
                patch_section(vault, "ld-concurrency", "## Run log", f"- {tag} t{i}", position="end_of_section")

        threads = [threading.Thread(target=_t, args=(t,)) for t in ("thrA", "thrB")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        text2 = read_note(vault, "ld-concurrency")
        missing = [f"- {t} t{i}" for t in ("thrA", "thrB") for i in range(5) if f"- {t} t{i}" not in text2]
        check("all 10 concurrent thread run-log lines present", not missing, f"missing={missing}")

        # search + incident + frontmatter patch
        hits = search(vault, "grace")
        check("search finds the note by frontmatter/body text", any(h["lead_id"] == "ld-concurrency" for h in hits), str(hits))
        inc = write_incident(vault, "selftest incident", "direct-write incident body")
        check("write_incident writes a vault file", inc.exists(), inc.name)
        changed = patch_frontmatter(vault, "ld-concurrency", {"status": "drafted"})
        check("patch_frontmatter reports changed keys", changed == ["status"], str(changed))
        check("patch_frontmatter persisted the value",
              read_frontmatter(vault, "ld-concurrency").get("status") == "drafted")

    for r in results:
        print(f"PASS  {r}")
    for f in failures:
        print(f"FAIL  {f}")
    print(f"\nobsidian_client.py selftest: {len(results)} passed, {len(failures)} failed")
    return 1 if failures else 0


def main(argv: Iterable[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="direct file-I/O vault client")
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--vault", default="", help="vault path (for ad-hoc reads)")
    p.add_argument("--search", default="", help="run a search query against the vault")
    args = p.parse_args(list(argv) if argv is not None else None)
    if args.selftest or not (args.vault or args.search):
        return _selftest()
    if args.search:
        print(json.dumps(search(args.vault, args.search), indent=2))
        return 0
    print(json.dumps(read_frontmatter(args.vault, args.vault), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
