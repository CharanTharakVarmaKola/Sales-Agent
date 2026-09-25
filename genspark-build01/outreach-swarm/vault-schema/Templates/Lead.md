---
# vault-schema/Templates/Lead.md
# Canonical Lead note schema for the outreach vault.
# The ORDER of the keys below is the order ingest.py writes them.
# Section A ("lead fields") is written by ingest.py.
# Section B ("terminal fields") is owned by orchestrator/policy/reporter.py ONLY.
lead_id: ""
name: ""
title: ""
company: ""
domain: ""
email: ""
country: ""
industry: ""
pain_point: ""
angle: ""
source: ""
source_row: 0
unsubscribed: false
status: "new"
ingested_at: ""
# ---- Section B: TERMINAL fields (reporter.py is the only writer) ----
run_id: null
last_contacted_at: null
send_count: 0
compliance_verdict: null
delivery_status: null
final_status: null
---

# {{name}} — {{company}}

- **Role:** {{title}}
- **Industry:** {{industry}}
- **Country:** {{country}}
- **Pain point:** {{pain_point}}
- **Angle:** {{angle}}

## Evidence
<!-- copywriter appends the evidence graph walk here -->

## Run log
<!-- sender/reporter append run-log lines here -->
