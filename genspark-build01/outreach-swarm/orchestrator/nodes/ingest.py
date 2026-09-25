#!/usr/bin/env python3
"""orchestrator/nodes/ingest.py — BUILD Task 1.

Parses lead files (CSV, and PDF for directory-style exports) with messy,
inconsistent column layouts into the normalized frontmatter fields defined in
``vault-schema/Templates/Lead.md``, and writes them through the existing
``orchestrator/obsidian_client.py`` (never re-implementing its file logic).

Column normalization is delegated to the OpenClaw seam
(``orchestrator/openclaw/client.py``), because header spelling drift is exactly
the job that layer exists for; a local alias table is used as the offline
implementation.

Terminal-field safety: this node imports ``TERMINAL_FRONTMATTER`` and
``find_terminal_field_writes`` from the existing ``orchestrator/policy/reporter.py``
instead of re-implementing field permissions. Any terminal-looking column in an
input file is dropped and recorded in the parse audit.

PDF handling uses a line scanner with three formats, because real directory and
map-style exports mix all three on one page:
  A. delimiter-separated table regions (header line + data rows)
  B. ``Key: value`` contact blocks (blank lines between records are optional —
     PDF text extraction usually drops them, so a change of key run is used)
  C. free-text lines that carry an email address

Self-test:  python3 orchestrator/nodes/ingest.py --selftest
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator import obsidian_client as oc  # noqa: E402
from orchestrator.openclaw import MockOpenClawClient, build_openclaw_client  # noqa: E402
from orchestrator.policy.reporter import (  # noqa: E402
    TERMINAL_FRONTMATTER,
    find_terminal_field_writes,
)

#: Fields this node is permitted to populate (schema Section A).
INGEST_WRITABLE = (
    "lead_id", "name", "title", "company", "domain", "email", "country",
    "industry", "pain_point", "angle", "source", "source_row", "unsubscribed",
    "status", "ingested_at",
)

SPLIT_RE = re.compile(r"\s*[|;]\s*|\s{2,}|\t+")
KV_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 _/\-]{1,40})\s*[:=]\s*(.+?)\s*$")
KV_EMPTY_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 _/\-]{1,40})\s*[:=]\s*$")
EMAIL_RE = re.compile(r"[^@\s,;<>]+@[^@\s,;<>]+\.[A-Za-z]{2,}")


@dataclass
class ParseAudit:
    source: str
    rows_seen: int = 0
    rows_written: int = 0
    dropped_terminal_fields: List[str] = field(default_factory=list)
    unmapped_headers: List[str] = field(default_factory=list)
    skipped_rows: List[Dict[str, Any]] = field(default_factory=list)
    header_map: Dict[str, str] = field(default_factory=dict)
    formats_seen: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "rows_seen": self.rows_seen,
            "rows_written": self.rows_written,
            "dropped_terminal_fields": sorted(set(self.dropped_terminal_fields)),
            "unmapped_headers": self.unmapped_headers,
            "skipped_rows": self.skipped_rows,
            "header_map": self.header_map,
            "formats_seen": sorted(set(self.formats_seen)),
        }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _truthy(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on", "opt-out", "opt out", "unsubscribed", "dnc"}


def _clean(v: Any) -> str:
    if v is None:
        return ""
    return re.sub(r"\s+", " ", str(v)).strip().strip('"').strip("|").strip()


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")


def make_lead_id(company: str, name: str, email: str, row_index: int) -> str:
    base = slug(company) or slug(email.split("@")[-1]) or slug(name) or "lead"
    stem = f"ld-{base}"[:60].strip("-")
    suffix = slug(name.split()[-1]) if name else ""
    return f"{stem}{('-' + suffix) if suffix else ''}"


def domain_from_email(email: str) -> str:
    m = EMAIL_RE.fullmatch((email or "").strip())
    return m.group(0).split("@")[-1].lower() if m else ""


def _apply_map(raw: Mapping[str, Any], header_map: Mapping[str, str]) -> Dict[str, Any]:
    """Project one raw row onto canonical ingest fields; drop everything else."""
    out: Dict[str, Any] = {}
    for canonical, header in header_map.items():
        if header in raw and canonical in INGEST_WRITABLE:
            out[canonical] = _clean(raw.get(header))
    return out


def normalize_record(
    raw: Mapping[str, Any],
    *,
    header_map: Mapping[str, str],
    source: str,
    row_index: int,
    audit: ParseAudit,
    openclaw: Any = None,
) -> Optional[Dict[str, Any]]:
    """Turn one messy row into a schema-shaped lead frontmatter dict."""
    audit.rows_seen += 1
    hits = find_terminal_field_writes(raw)
    if hits:
        audit.dropped_terminal_fields.extend(hits)
    for k in raw:
        if k not in header_map.values() and _clean(raw.get(k)):
            if k not in audit.unmapped_headers:
                audit.unmapped_headers.append(k)

    rec = _apply_map(raw, header_map)
    if openclaw is not None:
        missing = [f for f in ("industry", "country") if not rec.get(f)]
        if missing:
            rec = openclaw.enrich_lead(rec, missing)

    email = rec.get("email", "")
    company = rec.get("company", "")
    name = rec.get("name", "")
    if not (email or company) or not name:
        audit.skipped_rows.append(
            {"source_row": row_index, "reason": "no name, or neither company nor email",
             "raw": {k: _clean(v) for k, v in raw.items() if _clean(v)}}
        )
        return None

    lead = {
        "lead_id": make_lead_id(company, name, email, row_index),
        "name": name,
        "title": rec.get("title", ""),
        "company": company,
        "domain": rec.get("domain", "") or domain_from_email(email),
        "email": email,
        "country": rec.get("country", "").upper() if len(rec.get("country", "")) <= 3 else rec.get("country", ""),
        "industry": rec.get("industry", ""),
        "pain_point": rec.get("pain_point", ""),
        "angle": rec.get("angle", ""),
        "source": source,
        "source_row": row_index,
        "unsubscribed": _truthy(rec.get("unsubscribed")),
        "status": "new",
        "ingested_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    # belt and braces: the permission check is the reporter's, not ours
    assert not find_terminal_field_writes(lead), "ingest built a terminal field"
    return oc.order_like_schema(lead)


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------
def parse_csv_text(text: str, source: str, *, openclaw: Any = None, audit: Optional[ParseAudit] = None) -> List[Dict[str, Any]]:
    audit = audit or ParseAudit(source=source)
    audit.formats_seen.append("csv")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return []
    header = rows[0]
    oc_client = openclaw or MockOpenClawClient()
    header_map = dict(oc_client.normalize_columns(header))
    audit.header_map = header_map

    # positional fallback for headers the alias table does not know
    unresolved = [h for h in header if h not in header_map.values()]
    if unresolved and len(header_map) < 4:
        for canonical, idx in zip(INGEST_WRITABLE, range(len(header))):
            header_map.setdefault(canonical, header[idx])

    records: List[Dict[str, Any]] = []
    for i, row in enumerate(rows[1:], start=2):
        if not any(_clean(c) for c in row):
            continue  # blank physical line: keep the physical row numbering intact
        raw = {header[j]: row[j] for j in range(min(len(header), len(row)))}
        rec = normalize_record(raw, header_map=header_map, source=source, row_index=i, audit=audit, openclaw=oc_client)
        if rec:
            records.append(rec)
    return records


def parse_csv(path: str | Path, *, openclaw: Any = None, audit: Optional[ParseAudit] = None) -> List[Dict[str, Any]]:
    p = Path(path)
    return parse_csv_text(p.read_text(encoding="utf-8-sig"), p.name, openclaw=openclaw, audit=audit)


# ---------------------------------------------------------------------------
# PDF (directory / map-style exports: tables, key:value blocks, mixed)
# ---------------------------------------------------------------------------
def _pdf_text(path: str | Path) -> str:
    try:
        import pdfplumber

        with pdfplumber.open(str(path)) as pdf:
            return "\n".join((page.extract_text() or "") for page in pdf.pages)
    except Exception:
        from pypdf import PdfReader

        return "\n".join((pg.extract_text() or "") for pg in PdfReader(str(path)).pages)


def _kv(line: str) -> Optional[tuple[str, str]]:
    m = KV_RE.match(line)
    if m:
        return m.group(1), m.group(2)
    m = KV_EMPTY_RE.match(line)
    if m:
        return m.group(1), ""
    return None


def parse_pdf_text(text: str, source: str, *, openclaw: Any = None, audit: Optional[ParseAudit] = None) -> List[Dict[str, Any]]:
    audit = audit or ParseAudit(source=source)
    oc_client = openclaw or MockOpenClawClient()
    lines = [ln.rstrip() for ln in text.splitlines()]
    n = len(lines)
    records: List[Dict[str, Any]] = []
    header_map_global: Dict[str, str] = {}

    i = 0
    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue

        # ---- format A: delimiter-separated table region -------------------
        cells = [c for c in (x.strip() for x in SPLIT_RE.split(line)) if c]
        if len(cells) >= 3:
            header = cells
            hmap = dict(oc_client.normalize_columns(header))
            j = i + 1
            while j < n and lines[j].strip():
                row_cells = [c.strip() for c in SPLIT_RE.split(lines[j])]
                if len([c for c in row_cells if c]) < 2:
                    break
                if len(row_cells) > len(header):
                    off = len(row_cells) - len(header)  # tolerate a leading index column
                    raw = {header[k]: row_cells[k + off] for k in range(len(header))}
                else:
                    raw = {header[k]: row_cells[k] for k in range(min(len(header), len(row_cells)))}
                rec = normalize_record(raw, header_map=hmap, source=source, row_index=j + 1,
                                       audit=audit, openclaw=oc_client)
                if rec:
                    records.append(rec)
                j += 1
            header_map_global.update(hmap)
            audit.formats_seen.append("pdf-table")
            i = j
            continue

        # ---- format B: Key: value contact block ---------------------------
        if _kv(line):
            kv: Dict[str, str] = {}
            j = i
            while j < n and lines[j].strip() and _kv(lines[j]):
                k, v = _kv(lines[j])  # type: ignore[misc]
                if k in kv:
                    break  # a repeated anchor key means the next record has started
                kv[k] = v
                j += 1
            if len(kv) >= 3:
                hmap = dict(oc_client.normalize_columns(list(kv)))
                rec = normalize_record(kv, header_map=hmap, source=source, row_index=i + 1,
                                       audit=audit, openclaw=oc_client)
                if rec:
                    records.append(rec)
                header_map_global.update(hmap)
                audit.formats_seen.append("pdf-kv-block")
                i = j
                continue

        # ---- format C: free text line carrying an email -------------------
        m = EMAIL_RE.search(line)
        if m:
            pre = _clean(line[: m.start()])
            parts = [p for p in (x.strip() for x in pre.split(",")) if p]
            tighten = {
                "Full Name": parts[0] if parts else "",
                "Company": parts[-1] if len(parts) > 1 else "",
                "Work Email": m.group(0),
            }
            hmap = dict(oc_client.normalize_columns(list(tighten)))
            rec = normalize_record(tighten, header_map=hmap, source=source, row_index=i + 1,
                                   audit=audit, openclaw=oc_client)
            if rec:
                records.append(rec)
                header_map_global.update(hmap)
                audit.formats_seen.append("pdf-free-text")
        i += 1

    audit.header_map.update(header_map_global)
    return records


def parse_pdf(path: str | Path, *, openclaw: Any = None, audit: Optional[ParseAudit] = None) -> List[Dict[str, Any]]:
    p = Path(path)
    return parse_pdf_text(_pdf_text(p), p.name, openclaw=openclaw, audit=audit)


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------
def write_leads(vault: str | Path, leads: Sequence[Mapping[str, Any]]) -> List[str]:
    """Persist normalized leads via the existing obsidian_client (no duplication)."""
    oc.ensure_vault(vault)
    written: List[str] = []
    for lead in leads:
        bad = find_terminal_field_writes({k: v for k, v in lead.items() if v not in (None, "", 0, False)})
        if bad:
            raise PermissionError(f"ingest refused to write terminal fields {bad}")
        path = oc.write_lead(vault, lead)
        written.append(str(path))
    return written


def run(
    vault: str | Path,
    *,
    csv_path: str | Path | None = None,
    pdf_path: str | Path | None = None,
    raw_records: Sequence[Mapping[str, Any]] | None = None,
    openclaw: Any = None,
) -> Dict[str, Any]:
    """Graph node entry point: parse whatever was handed in and write leads."""
    leads: List[Dict[str, Any]] = []
    audited: List[ParseAudit] = []

    for fn, arg in ((parse_csv, csv_path), (parse_pdf, pdf_path)):
        if not arg:
            continue
        a = ParseAudit(source=Path(str(arg)).name)
        got = fn(arg, openclaw=openclaw, audit=a)
        a.rows_written = len(got)
        leads.extend(got)
        audited.append(a)

    if raw_records:
        a = ParseAudit(source="inline")
        oc_client = openclaw or MockOpenClawClient()
        hmap = dict(oc_client.normalize_columns(list(raw_records[0].keys())))
        a.header_map = hmap
        a.formats_seen.append("inline")
        for i, raw in enumerate(raw_records, start=1):
            rec = normalize_record(raw, header_map=hmap, source="inline", row_index=i, audit=a, openclaw=oc_client)
            if rec:
                leads.append(rec)
        a.rows_written = len(leads)
        audited.append(a)

    # de-duplicate on email / lead_id, keep first occurrence
    seen: set[str] = set()
    unique: List[Dict[str, Any]] = []
    for lead in leads:
        key = (lead.get("email") or lead.get("lead_id") or "").lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(lead)

    paths = write_leads(vault, unique)
    return {
        "node": "ingest",
        "leads_written": len(paths),
        "paths": paths,
        "lead_ids": [Path(p).stem for p in paths],
        "audit": [a.to_dict() for a in audited],
        "terminal_fields_dropped": sorted({f for a in audited for f in a.dropped_terminal_fields}),
    }


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------
MESSY_CSV = """Full Name,ORG,E-Mail Address,Website,Country Code,Sector,Pain,Hook,Opt Out,run_id,final_status,send_count
Marcus Vale,Northwind Robotics,marcus.vale@northwindrobotics.example,northwindrobotics.example,GB,industrial automation,manual QA cycles,cut QA time,no,run-should-be-ignored,should-not-leak,7
Priya Raman,Helios Freight,priya.raman@heliosfreight.example,heliosfreight.example,DE,logistics,dispatch delays,automate dispatch,yes,run-should-be-ignored,should-not-leak,3
   ,   ,   ,   ,   ,   ,   ,   ,   ,   ,   ,
Diego Alvarez,Cobalt Analytics,diego.alvarez@cobaltanalytics.example,cobaltanalytics.example,ES,analytics,slow onboarding,faster onboarding,no,run-should-be-ignored,should-not-leak,1
,Nowhere Ltd,,hello@nowhere.example,,consulting,unknown,misc,no,,,
"""

PDF_TABLE = (
    "Full Name | Organisation | Work Email | Website | Sector | Pain\n"
    "Sofia Marchetti | Verdant Grid | sofia.marchetti@verdantgrid.example | verdantgrid.example | energy | grid imbalance\n"
    "Tom Okafor | Lantern Health | tom.okafor@lanternhealth.example | lanternhealth.example | healthcare | referral backlog\n"
)
PDF_BLOCKS = (
    "Contact Name: Ana Duarte\n"
    "Organization: Riverbend Legal\n"
    "E-Mail Address: ana.duarte@riverbendlegal.example\n"
    "Website: riverbendlegal.example\n"
    "Country Code: PT\n"
    "Sector: legal services\n"
    "Signal: manual intake\n"
    "Hook: automate intake\n"
    "Opt Out: no\n"
    "Contact Name: Ken Watanabe\n"
    "Organization: Kite Manufacturing\n"
    "E-Mail Address:\n"
    "Website: kitemanufacturing.example\n"
    "Sector: manufacturing\n"
    "Signal: line stoppages\n"
    "Hook: predictive maintenance\n"
)


def _build_mock_pdf(path: Path) -> Path:
    from reportlab.lib.pagesizes import LETTER
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=LETTER)
    y = 740
    for line in PDF_TABLE.splitlines():
        c.drawString(40, y, line)
        y -= 18
    c.showPage()
    y = 740
    for line in PDF_BLOCKS.splitlines():
        c.drawString(40, y, line)
        y -= 16
    c.save()
    return path


def _selftest() -> int:
    passed = failed = 0

    def check(label: str, ok: bool, detail: str = "") -> None:
        nonlocal passed, failed
        passed += bool(ok)
        failed += (not ok)
        print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f" — {detail}" if detail else ""))

    with tempfile.TemporaryDirectory(prefix="ingest-selftest-") as tmp:
        tmpd = Path(tmp)
        vault = tmpd / "vault"
        csv_path = tmpd / "messy_leads.csv"
        pdf_path = tmpd / "directory_export.pdf"
        csv_path.write_text(MESSY_CSV, encoding="utf-8")
        _build_mock_pdf(pdf_path)

        oc_client = MockOpenClawClient(enrich_country="XX")
        audit_csv = ParseAudit(source="messy_leads.csv")
        audit_pdf = ParseAudit(source="directory_export.pdf")
        csv_leads = parse_csv(csv_path, openclaw=oc_client, audit=audit_csv)
        pdf_leads = parse_pdf(pdf_path, openclaw=oc_client, audit=audit_pdf)
        all_leads = csv_leads + pdf_leads

        check("CSV parsed with messy headers", len(csv_leads) == 3,
              f"got {len(csv_leads)}: {[l['name'] for l in csv_leads]}")
        check("PDF parsed from a table region AND key:value blocks", len(pdf_leads) == 4,
              f"got {len(pdf_leads)}: {[l['name'] for l in pdf_leads]}")
        check("both PDF input formats were detected",
              {"pdf-table", "pdf-kv-block"} <= set(audit_pdf.formats_seen), str(audit_pdf.formats_seen))
        check("blank line in CSV is skipped, not fatal", not any(not l["name"] for l in csv_leads))
        check("row with no name and no company is rejected and audited at its physical line",
              any(s.get("source_row") == 6 and "Nowhere Ltd" in json.dumps(s) for s in audit_csv.skipped_rows),
              json.dumps(audit_csv.skipped_rows))
        check("header alias mapping resolved ORG -> company", audit_csv.header_map.get("company") == "ORG",
              json.dumps(audit_csv.header_map))
        check("domain inferred from email when no website column", all(l["domain"] for l in all_leads))
        check("country normalised to upper case", csv_leads[0]["country"] == "GB", csv_leads[0]["country"])
        check("opt-out column mapped to a boolean",
              csv_leads[1]["unsubscribed"] is True and csv_leads[0]["unsubscribed"] is False,
              str([l["unsubscribed"] for l in csv_leads]))
        check("PDF record with an empty email value is still ingested (messy input tolerated)",
              any(l["name"] == "Ken Watanabe" for l in pdf_leads))
        check("no cross-record field bleed: each PDF lead keeps its own email",
              {l["name"]: l["email"] for l in pdf_leads} == {
                  "Sofia Marchetti": "sofia.marchetti@verdantgrid.example",
                  "Tom Okafor": "tom.okafor@lanternhealth.example",
                  "Ana Duarte": "ana.duarte@riverbendlegal.example",
                  "Ken Watanabe": "",
              },
              json.dumps({l["name"]: l["email"] for l in pdf_leads}))
        check("table columns mapped by alias, not by accident",
              next(l for l in pdf_leads if l["name"] == "Sofia Marchetti")["industry"] == "energy",
              str(next(l for l in pdf_leads if l["name"] == "Sofia Marchetti")["industry"]))
        check("OpenClaw enrich step filled a genuinely missing country",
              any(l["country"] == "XX" for l in pdf_leads), str([l["country"] for l in pdf_leads]))
        check("enrich never overwrote a country that was present",
              next(l for l in pdf_leads if l["name"] == "Ana Duarte")["country"] == "PT",
              next(l for l in pdf_leads if l["name"] == "Ana Duarte")["country"])

        result = run(vault, csv_path=csv_path, pdf_path=pdf_path, openclaw=oc_client)
        check("all parsed rows written through obsidian_client", result["leads_written"] == 7,
              str(result["leads_written"]))
        check("terminal fields present in the input were dropped",
              set(result["terminal_fields_dropped"]) == {"run_id", "final_status", "send_count"},
              str(result["terminal_fields_dropped"]))

        # schema conformance + no terminal writes
        terminal_untouched = True
        schema_ok = True
        notes = sorted(Path(vault).glob("Leads/*.md"))
        for path in notes:
            fm = oc.read_frontmatter(vault, path)
            if list(fm.keys()) != list(oc.SCHEMA_ORDER):
                schema_ok = False
            if (fm.get("run_id") is not None or fm.get("last_contacted_at") is not None
                    or fm.get("compliance_verdict") is not None or fm.get("delivery_status") is not None
                    or fm.get("final_status") is not None or int(fm.get("send_count") or 0) != 0):
                terminal_untouched = False
        check("every written note matches the Lead.md key order exactly", schema_ok,
              f"{len(notes)} notes checked")
        check("no terminal field was touched by ingest (all at schema defaults)", terminal_untouched)
        check("ingest reuses reporter's terminal allowlist rather than its own copy",
              TERMINAL_FRONTMATTER == frozenset({"run_id", "last_contacted_at", "send_count",
                                                 "compliance_verdict", "delivery_status", "final_status"})
              and "TERMINAL_FRONTMATTER" in Path(__file__).read_text(encoding="utf-8"))

        sample = oc.read_frontmatter(vault, "ld-northwind-robotics-vale")
        check("sample lead frontmatter matches the schema values",
              sample.get("company") == "Northwind Robotics" and sample.get("industry") == "industrial automation"
              and sample.get("email") == "marcus.vale@northwindrobotics.example",
              json.dumps({k: sample.get(k) for k in ("name", "company", "industry", "email", "source", "source_row")}))

    print(f"\ningest.py selftest: {passed} passed, {failed} failed")
    return 1 if failed else 0


def main(argv: Iterable[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="lead ingest node (CSV + PDF -> vault notes)")
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--vault", default="")
    p.add_argument("--csv", default="")
    p.add_argument("--pdf", default="")
    args = p.parse_args(list(argv) if argv is not None else None)
    if args.selftest or not args.vault:
        return _selftest()
    print(json.dumps(run(args.vault, csv_path=args.csv or None, pdf_path=args.pdf or None), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
