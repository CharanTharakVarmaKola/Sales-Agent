"""Obsidian vault client — direct file I/O, no REST, no plugin, no credentials.

Every read/write goes to plain .md files on disk. patch_section() uses a
cross-process flock keyed on the note path, writes atomically via os.replace.
"""
from __future__ import annotations

import argparse
try:
    import fcntl
except ImportError:
    fcntl = None
import os
import re
import sys
import tempfile
from contextlib import contextmanager

DEFAULT_VAULT = os.environ.get("OBSIDIAN_VAULT", os.path.join(os.getcwd(), "vault"))

FM_PATTERN = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)


@contextmanager
def _file_lock(note_path: str):
    lock_path = note_path + ".lock"
    with open(lock_path, "w") as fh:
        if fcntl:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _atomic_write(path: str, content: str) -> None:
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(content)
    os.replace(tmp, path)


def ensure_vault(path: str | None = None, template: str | None = None) -> str:
    vault = path or DEFAULT_VAULT
    os.makedirs(os.path.join(vault, "Templates"), exist_ok=True)
    os.makedirs(os.path.join(vault, "Leads"), exist_ok=True)
    template_path = os.path.join(vault, "Templates", "Lead.md")
    if not os.path.exists(template_path) and template:
        _atomic_write(template_path, template)
    return vault


def _slug(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", (name or "unnamed").strip()).strip("-")
    return slug or "unnamed"


def note_path_for(vault: str, lead: dict) -> str:
    key = lead.get("lead_id") or lead.get("name") or "unnamed"
    return os.path.join(vault, "Leads", _slug(key) + ".md")


def resolve_note(vault: str, lead_id_or_name: str) -> str | None:
    p = os.path.join(vault, "Leads", _slug(lead_id_or_name) + ".md")
    return p if os.path.exists(p) else None


def render_note(lead: dict, vault: str | None = None, template: str | None = None) -> str:
    vault = vault or DEFAULT_VAULT
    tpl_path = os.path.join(vault, "Templates", "Lead.md")
    tpl = template or (open(tpl_path, encoding="utf-8").read() if os.path.exists(tpl_path) else "")
    if not tpl.startswith("---"):
        raise ValueError("Lead.md template missing frontmatter")
    lines = []
    in_b = False
    for line in tpl.splitlines():
        if line.strip() == "# Section B":
            in_b = True
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):(.*)$", line)
        if m:
            key = m.group(1)
            if key in lead:
                lines.append(f"{key}: {lead[key]}")
                continue
        lines.append(line)
    return "\n".join(lines) + "\n"


def write_lead(vault: str, lead: dict) -> str:
    ensure_vault(vault)
    path = note_path_for(vault, lead)
    _atomic_write(path, render_note(lead, vault))
    return path


def read_note(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def read_frontmatter(path: str) -> dict:
    text = read_note(path)
    m = FM_PATTERN.match(text)
    if not m:
        return {}
    fm = {}
    try:
        import yaml  # type: ignore
        parsed = yaml.safe_load(m.group(1))
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    for line in m.group(1).splitlines():
        km = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$", line)
        if km:
            raw = km.group(2).strip()
            if raw.lower() in ("true", "false"):
                fm[km.group(1)] = raw.lower() == "true"
            elif re.fullmatch(r"-?\d+", raw):
                fm[km.group(1)] = int(raw)
            else:
                fm[km.group(1)] = raw.strip('"')
    return fm


def patch_frontmatter(path: str, key: str, value) -> None:
    with _file_lock(path):
        text = read_note(path)
        m = FM_PATTERN.match(text)
        block = m.group(1) if m else ""
        lines, found = [], False
        for line in block.splitlines():
            if re.match(rf"^{re.escape(key)}:", line):
                lines.append(f"{key}: {value}")
                found = True
            else:
                lines.append(line)
        if not found:
            lines.append(f"{key}: {value}")
        new_block = "\n".join(lines)
        if m:
            new_text = text.replace(m.group(0), f"---\n{new_block}\n---\n", 1)
        else:
            new_text = f"---\n{new_block}\n---\n" + text
        _atomic_write(path, new_text)


def _find_heading(text: str, heading: str, path: str = "<note>") -> int:
    pat = re.compile(rf"^##\s+{re.escape(heading)}\s*$", re.MULTILINE)
    m = pat.search(text)
    if not m:
        raise ValueError(f"heading '## {heading}' not found in {path}")
    return m.end()


def patch_section(path: str, heading: str, content: str, prepend: bool = True) -> None:
    """Insert content under '## <heading>' with a cross-process lock + atomic write."""
    with _file_lock(path):
        text = read_note(path)
        idx = _find_heading(text, heading, path)
        # find end of this section (next '## ' heading or EOF)
        nxt = re.search(r"^##\s+", text[idx:], re.MULTILINE)
        end = idx + nxt.start() if nxt else len(text)
        block = content if content.endswith("\n") else content + "\n"
        if prepend:
            new_text = text[:idx] + "\n" + block + text[idx + 1:end] + text[end:]
        else:
            new_text = text[:end].rstrip("\n") + "\n" + block + text[end:]
        _atomic_write(path, new_text)


def append_run_log(path: str, line: str) -> None:
    stamp_line = f"- {line}\n"
    patch_section(path, "Run log", stamp_line, prepend=False)


def write_incident(path: str, incident_id: str, text: str) -> None:
    patch_section(path, "Incidents", f"- [{incident_id}] {text}\n", prepend=False)


def list_notes(vault: str, folder: str = "Leads") -> list[str]:
    d = os.path.join(vault, folder)
    if not os.path.isdir(d):
        return []
    return sorted(os.path.join(d, f) for f in os.listdir(d) if f.endswith(".md"))


def search(vault: str, query: str) -> list[str]:
    """Recursive scan of frontmatter fields and body text — no index."""
    hits = []
    for root, _dirs, files in os.walk(vault):
        for f in files:
            if not f.endswith(".md"):
                continue
            p = os.path.join(root, f)
            if query.lower() in read_note(p).lower():
                hits.append(p)
    return hits


# ---------------------------------------------------------------- self-test
def _selftest() -> int:
    import shutil
    import threading
    import subprocess
    import sys

    passed = failed = 0

    def check(name, cond):
        nonlocal passed, failed
        if cond:
            passed += 1
        else:
            failed += 1
            print(f"  FAIL: {name}")

    tmp = tempfile.mkdtemp(prefix="vault-selftest-")
    try:
        vault = ensure_vault(os.path.join(tmp, "v"), template=open(
            os.path.join(os.path.dirname(__file__), "..", "vault-schema", "Templates", "Lead.md"),
            encoding="utf-8").read())
        check("ensure_vault creates Templates + Leads", os.path.isdir(os.path.join(vault, "Leads")))

        p = write_lead(vault, {"lead_id": "l-1", "name": "Test Person", "company": "Acme",
                               "email": "t@acme.io", "industry": "SaaS"})
        check("write_lead returns a path that exists", os.path.exists(p))
        fm = read_frontmatter(p)
        check("frontmatter round-trips name", fm.get("name") == "Test Person")
        check("terminal field default false", fm.get("unsubscribed") is False)

        patch_frontmatter(p, "send_count", 1)
        check("patch_frontmatter updates value", read_frontmatter(p)["send_count"] == 1)
        check("frontmatter block survives patch", read_note(p).startswith("---"))

        patch_section(p, "Evidence", "ev-1")
        check("patch_section inserts under heading", "ev-1" in read_note(p))
        try:
            patch_section(p, "No Such Heading", "x")
            check("missing heading raises", False)
        except ValueError:
            check("missing heading raises", True)

        # cross-process concurrency: 2 processes x 3 patch_section calls
        procs = [subprocess.Popen([sys.executable, os.path.abspath(__file__), "--proc-worker", p, str(i)],
                                  cwd=tmp) for i in range(2)]
        for pr in procs:
            pr.wait()
        check("6 process sections all landed (no torn write)",
              read_note(p).count("proc-") == 6)
        check("exactly one Evidence heading", read_note(p).count("## Evidence") == 1)

        # thread concurrency: 2 threads x 5 run-log lines
        def thread_worker(tid):
            for i in range(5):
                append_run_log(p, f"thread-{tid}-{i}")
        ts = [threading.Thread(target=thread_worker, args=(t,)) for t in range(2)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        body = read_note(p)
        check("10 thread run-log lines all landed",
              all(f"thread-{t}-{i}" in body for t in range(2) for i in range(5)))
        check("exactly one Run log heading", body.count("## Run log") == 1)

        write_incident(p, "INC-1", "test incident")
        check("write_incident lands under Incidents", "[INC-1] test incident" in read_note(p))

        check("resolve_note finds existing lead", resolve_note(vault, "l-1") == p)
        check("resolve_note returns None for missing", resolve_note(vault, "ghost") is None)
        check("list_notes sees the lead", p in list_notes(vault))
        check("search finds by body text", p in search(vault, "test incident"))
        check("search finds by frontmatter", p in search(vault, "Acme"))
        check("atomic write leaves no .tmp debris",
              not [f for f in os.listdir(os.path.dirname(p)) if f.endswith(".tmp")])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--proc-worker":
        _p, _i = sys.argv[2], sys.argv[3]
        for _k in range(3):
            patch_section(_p, "Evidence", f"proc-{_i}-{_k}\n")
        sys.exit(0)
    sys.exit(_selftest())
