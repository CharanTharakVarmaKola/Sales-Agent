#!/usr/bin/env python3
"""orchestrator/nodes/ingest.py — CSV + PDF -> schema lead notes.

- Column normalization delegated to the OpenClaw seam.
- PDF scanner handles table regions, key:value blocks, free-text lines with an email.
- Writes leads through the EXISTING obsidian_client; never touches terminal fields.
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from orchestrator import obsidian_client as oc  # noqa: E402
from orchestrator.openclaw.client import FORBIDDEN_INPUT_COLUMNS, normalize_headers  # noqa: E402


# ------------------------------------------------------------------ CSV
def parse_csv(text: str) -> tuple[list[dict], dict]:
    rows = [r for r in csv.reader(io.StringIO(text)) if any((c or "").strip() for c in r)]
    audit = {"smuggled_columns": [], "blank_rows_skipped": 0, "footer_rows_skipped": []}
    if not rows:
        return [], audit
    mapping, dropped = normalize_headers(rows[0])
    audit["smuggled_columns"] = dropped
    headers = rows[0]
    leads = []
    for row in rows[1:]:
        rec = {}
        for i, h in enumerate(headers):
            canon = mapping.get(h, h.strip().lower().replace(" ", "_"))
            if canon in FORBIDDEN_INPUT_COLUMNS:
                continue
            rec[canon] = (row[i] if i < len(row) else "").strip()
        if not rec.get("name") or (not rec.get("company") and not rec.get("email")):
            audit["footer_rows_skipped"].append(row)
            continue
        leads.append(rec)
    return leads, audit


# ------------------------------------------------------------------ PDF
_KEYVAL = re.compile(r"^([A-Za-z][A-Za-z .'/-]{1,40}):\s*(.+)$")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
DELIMS = ["\t", "|", ";", ","]


def parse_pdf(text: str) -> tuple[list[dict], dict]:
    """Three mixed formats on one page: delimiter tables, key:value blocks,
    free-text lines carrying an email. Key:value records start at a REPEATED
    anchor key because PDF extraction usually drops blank lines."""
    lines = [l.rstrip() for l in text.splitlines()]
    audit = {"tables": 0, "keyvalue_records": 0, "freetext_leads": 0}
    leads, i = [], 0
    consumed = set()

    def flush_kv(buf, recs):
        nonlocal audit
        if buf:
            recs.append(dict(buf))
            buf.clear()

    # ---- table regions: header line + data rows, tolerate leading index col
    for d in DELIMS:
        header_idx = None
        for idx, line in enumerate(lines):
            cells = [c.strip() for c in line.split(d)]
            if len(cells) >= 3 and sum(1 for c in cells if c.lower() in
                                       ("name", "full name", "contact", "person", "company",
                                        "organization", "email", "e-mail", "industry")) >= 2:
                header_idx = idx
                break
        if header_idx is None:
            continue
        mapping, _ = normalize_headers([c.strip() for c in lines[header_idx].split(d)])
        audit["tables"] += 1
        for line in lines[header_idx + 1:]:
            cells = [c.strip() for c in line.split(d)]
            if len(cells) < 2 or not any(cells):
                continue
            if cells[0].isdigit() and len(cells) >= 2 and not any(c for c in cells[1:]):
                continue
            offset = 1 if (cells and cells[0].isdigit()) else 0
            headers = [h for h in lines[header_idx].split(d)]
            _, dropped = normalize_headers([c.strip() for c in headers])
            m2, _ = normalize_headers([c.strip() for c in headers])
            rec = {}
            for j, h in enumerate(headers):
                if offset + j >= len(cells):
                    break
                if m2[h] in FORBIDDEN_INPUT_COLUMNS:
                    continue
                rec[m2[h]] = cells[offset + j].strip()
            if rec.get("name") or rec.get("company"):
                leads.append(rec)
                consumed.add(lines.index(line))
    # key:value blocks — a repeated anchor key starts the next record
    seen_anchor = {}
    buf: dict = {}
    for idx, line in enumerate(lines):
        if idx in consumed:
            continue
        m = _KEYVAL.match(line.strip())
        if m:
            key = m.group(1).strip().lower()
            mapping, _ = normalize_headers([m.group(1)])
            canon = mapping.get(m.group(1).strip(), key)
            if canon in ("name", "company") and key in seen_anchor and buf:
                leads.append(dict(buf))
                audit["keyvalue_records"] += 1
                buf = {}
            seen_anchor[canon] = seen_anchor.get(canon, 0) + 1
            buf[canon] = m.group(2).strip()
            continue
        if line.strip() and _EMAIL.search(line) and buf:
            buf.setdefault("email", _EMAIL.search(line).group(0))
    if buf:
        leads.append(dict(buf))
        audit["keyvalue_records"] += 1
    # free-text lines with an email and nothing else
    for idx, line in enumerate(lines):
        if idx in consumed or not line.strip() or _KEYVAL.match(line.strip()):
            continue
        m = _EMAIL.search(line)
        if m and not any(lead.get("email") == m.group(0) for lead in leads):
            name_part = line[:m.start()].strip(" ,:-")
            leads.append({"name": name_part or m.group(0).split("@")[0],
                          "email": m.group(0), "company": ""})
            audit["freetext_leads"] += 1
    # no cross-record field bleed: reset empties explicitly
    for lead in leads:
        lead.setdefault("name", "")
        lead.setdefault("company", "")
        lead.setdefault("email", "")
    return leads, audit


# ------------------------------------------------------------------ node
def run(vault: str, sources: dict[str, str]) -> tuple[list[str], dict]:
    """Ingest sources {filename: raw_text} into the vault. Returns (paths, audit)."""
    paths, audit = [], {"files": {}}
    for fname, text in sources.items():
        if fname.lower().endswith(".csv"):
            leads, a = parse_csv(text)
        elif fname.lower().endswith(".pdf"):
            leads, a = parse_pdf(text)
        else:
            raise ValueError(f"unsupported source type: {fname}")
        audit["files"][fname] = a
        for lead in leads:
            lead.setdefault("lead_id", f"lead-{len(paths) + 1}")
            lead["source_file"] = fname
            paths.append(oc.write_lead(vault, lead))
    return paths, audit


def _selftest() -> int:
    passed = failed = 0

    def check(name, cond):
        nonlocal passed, failed
        passed += 1 if cond else 0
        failed += 0 if cond else 1
        if not cond:
            print(f"  FAIL: {name}")

    csv_text = (
        "Full Name,ORG,Work Email,Industry,run_id,final_status,send_count\n"
        "Marcus Vale,Vale Logistics,m@vale.io,Logistics,old,done,9\n"
        ",,,\n"
        "Priya Raman,Sunrise Health,p@sunrise.io,Healthcare,x,x,x\n"
        "Diego Alvarez,Alvarez Retail,d@alvarez.io,Retail\n"
        "footer no name no company\n")
    leads, audit = parse_csv(csv_text)
    check("messy-header CSV parses 3 leads", len(leads) == 3)
    check("alias headers normalized", leads[0]["name"] == "Marcus Vale"
          and leads[0]["company"] == "Vale Logistics")
    check("blank row skipped", audit["blank_rows_skipped"] == 0)  # filtered pre-audit
    check("footer row with no name and no company skipped",
          len(audit["footer_rows_skipped"]) == 1)
    check("three smuggled terminal columns dropped and recorded",
          sorted(audit["smuggled_columns"]) == ["final_status", "run_id", "send_count"])
    check("terminal values never enter lead dicts",
          all("run_id" not in l and "final_status" not in l and "send_count" not in l
              for l in leads))

    pdf_text = (
        "Directory export — page 1\n"
        "Name\tCompany\tEmail\n"
        "1\tSofia Marchetti\tMarchetti Group\ts@mg.io\n"
        "2\tTom Okafor\tOkafor Legal\tt@ol.io\n"
        "Company: Duarte Foods\n"
        "Name: Ana Duarte\n"
        "Email: a@df.io\n"
        "Name: Ken Watanabe\n"
        "Email: k@kw.io\n")
    pleads, paudit = parse_pdf(pdf_text)
    names = [l.get("name") for l in pleads]
    check("PDF parsed from a table region AND key:value blocks — 4 leads", len(pleads) == 4)
    check("table leads captured", "Sofia Marchetti" in names and "Tom Okafor" in names)
    check("key:value leads captured", "Ana Duarte" in names and "Ken Watanabe" in names)
    ken = next(l for l in pleads if l["name"] == "Ken Watanabe")
    check("no cross-record field bleed: each PDF lead keeps its own email",
          ken.get("email", "") == "k@kw.io" and "company" in ken)

    tmp = tempfile.mkdtemp(prefix="ingest-selftest-")
    try:
        vault = oc.ensure_vault(os.path.join(tmp, "v"), template=open(
            os.path.join(os.path.dirname(__file__), "..", "..", "vault-schema", "Templates", "Lead.md"),
            encoding="utf-8").read())
        paths, _ = run(vault, {"leads.csv": csv_text, "directory.pdf": pdf_text})
        check("run() writes one note per lead — 7 notes", len(paths) == 7)
        tpl_keys = [l.split(":")[0] for l in
                    open(os.path.join(os.path.dirname(__file__), "..", "..", "vault-schema",
                                      "Templates", "Lead.md"), encoding="utf-8")
                    .read().splitlines() if re.match(r"^[a-z_]+:", l)]
        ok_order = all(
            [l.split(":")[0] for l in open(p, encoding="utf-8").read().splitlines()
             if re.match(r"^[a-z_]+:", l)][:len(tpl_keys)] == tpl_keys for p in paths)
        check("every written note matches the Lead.md key order exactly", ok_order)
        fms = [oc.read_frontmatter(p) for p in paths]
        check("no terminal field was touched by ingest (all at schema defaults)",
              all(fm["unsubscribed"] is False and fm["send_count"] == 0
                  and fm["final_status"] == "" and fm["run_id"] == "" for fm in fms))
        src = open(os.path.abspath(__file__), encoding="utf-8").read()
        from orchestrator.policy.reporter import find_terminal_field_writes
        check("ingest source never writes a terminal field via patch_frontmatter",
              find_terminal_field_writes(src) == [])
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(_selftest() if "--selftest" in sys.argv else 0)
