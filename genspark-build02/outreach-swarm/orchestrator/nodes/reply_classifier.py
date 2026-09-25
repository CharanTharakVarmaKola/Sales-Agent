#!/usr/bin/env python3
"""orchestrator/nodes/reply_classifier.py — six-way reply intent classification.

Ports the conversation-stage logic from SalesGPT's determine_conversation_stage()
(salesgpt/agents.py:134, stage dictionary at :66) — ONLY that stage logic; the
surrounding SalesGPT runtime is not adopted. The stage dictionary is copied and
extended into a six-way intent classification:

    interested | not_interested | objection | auto_reply | bounce | unsubscribe

On `unsubscribe` or `bounce` this node calls the EXISTING reporter.py write path
(set_terminal_field) to set the relevant TERMINAL_FRONTMATTER field — it never
writes frontmatter directly and never bypasses the write-guard.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from orchestrator import obsidian_client as oc  # noqa: E402
from orchestrator.policy import reporter  # noqa: E402

INTENTS = ("interested", "not_interested", "objection", "auto_reply",
           "bounce", "unsubscribe")

# ---------------------------------------------------------------- SalesGPT port
# Copied from salesgpt/agents.py:66 — the conversation-stage dictionary.
CONVERSATION_STAGES = {
    "1": "Introduction: Start the conversation by introducing yourself and your company. Be polite and respectful while keeping the tone of the conversation positive.",
    "2": "Qualification: Qualify the prospect by confirming that they are the right person to talk to regarding your product/service.",
    "3": "Value proposition: Briefly explain how your product/service can benefit the prospect.",
    "4": "Needs analysis: Ask open-ended questions to uncover the prospect's needs and pain points.",
    "5": "Solution presentation: Based on the prospect's needs, present your product/service as the solution that can address their pain points.",
    "6": "Objection handling: Address any objections that the prospect may have regarding your product/service.",
    "7": "Close: Ask for the sale or next step, and propose a follow-up action.",
}

# Ported from salesgpt/agents.py:134 — determine_conversation_stage() logic:
# pick the stage whose description has the most keyword overlap with the
# conversation. Here the same max-overlap scoring determines the reply's stage,
# and the stage determines the intent when the reply shows no explicit signal.
_STAGE_KEYWORDS = {
    "1": ["introduction", "introduce", "who are you", "first time", "reaching out"],
    "2": ["who is this", "right person", "qualify", "how did you get"],
    "3": ["benefit", "value", "what do you offer", "how can you help"],
    "4": ["challenge", "pain", "problem", "need", "struggle", "issue"],
    "5": ["demo", "trial", "show me", "presentation", "solution", "pricing"],
    "6": ["too expensive", "not sure", "concern", "worried", "alternative", "objection"],
    "7": ["next steps", "contract", "sign", "schedule a call", "book", "when can we start"],
}


def determine_conversation_stage(conversation_text: str) -> str:
    """SalesGPT agents.py:134 logic — max keyword-overlap stage selection."""
    text = conversation_text.lower()
    best_stage, best_score = "1", 0
    for stage, keywords in _STAGE_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text)
        if score > best_score:
            best_stage, best_score = stage, score
    return best_stage


# ---------------------------------------------------------------- classifier
_PATTERNS = [
    ("unsubscribe", re.compile(
        r"\b(unsubscribe|remove me|take me off|opt.?out|stop emailing|"
        r"do not email|don.?t contact me)\b", re.I)),
    ("bounce", re.compile(
        r"^(undeliverable|delivery (status|failure)|mail delivery failed|"
        r"address (not found|does not exist)|mailbox (full|unavailable)|"
        r"permanent error|hard bounce|returned mail|user unknown|"
        r"no longer (at|with) this (address|company)|recipient rejected)", re.I | re.M)),
    ("auto_reply", re.compile(
        r"^(auto(tomated)? ?(reply|response)|out of office|ooo|on vacation|"
        r"away from (the )?office|automatic message|delivery status notification)", re.I | re.M)),
    ("not_interested", re.compile(
        r"\b(not interested|no thank|no thanks|pass(ing)? on this|"
        r"not a fit|not the right time|we.?re all set|no need)\b", re.I)),
    ("objection", re.compile(
        r"\b(too expensive|budget (is )?(tight|frozen)|concern(ed)? about|"
        r"not sure (about|that)|we already use|locked (in|into)|"
        r"worried about|wait until|revisit (in|next))\b", re.I)),
    ("interested", re.compile(
        r"\b(interested|sounds (great|good|interesting)|tell me more|"
        r"let.?s (talk|chat|schedule)|happy to|can you send|"
        r"(what.?s|what is the) (next step|price)|book a (call|demo)|"
        r"yes(,)? (let.?s|please))\b", re.I)),
]


def classify_reply(text: str, threshold: float = 1.0) -> dict:
    """Six-way intent classification with an ambiguity guard.

    Returns {'intent', 'stage', 'confidence', 'ambiguous'} — ambiguous=True when
    no pattern matches strongly enough to force a confident label.
    """
    text = (text or "").strip()
    scores = {}
    for intent, pat in _PATTERNS:
        m = list(pat.finditer(text))
        if m:
            scores[intent] = len(m)
    if not scores:
        # SalesGPT port: fall back to conversation-stage analysis
        stage = determine_conversation_stage(text)
        return {"intent": "interested" if stage in ("5", "7") else (
                    "objection" if stage == "6" else "auto_reply"),
                "stage": stage, "confidence": 0.0, "ambiguous": True}
    best = max(scores, key=scores.get)
    top = scores[best]
    ties = [k for k, v in scores.items() if v == top]
    # unsubscribe/bounce win ties — terminal states must never lose to a tiebreak
    if len(ties) > 1:
        for terminal in ("unsubscribe", "bounce"):
            if terminal in ties:
                best = terminal
                break
        else:
            return {"intent": best, "stage": determine_conversation_stage(text),
                    "confidence": top, "ambiguous": True}
    return {"intent": best, "stage": determine_conversation_stage(text),
            "confidence": top / sum(scores.values()), "ambiguous": False}


def run(lead_note: str, reply_text: str, run_id: str = "reply-run-1") -> dict:
    """Classify a reply against a lead note; write terminal fields via reporter."""
    result = classify_reply(reply_text)
    intent = result["intent"]
    if intent == "unsubscribe":
        reporter.set_terminal_field(lead_note, "unsubscribed", True,
                                    reason=f"reply classified unsubscribe ({run_id})")
        reporter.set_terminal_field(lead_note, "reply_intent", "unsubscribe")
    elif intent == "bounce":
        reporter.set_terminal_field(lead_note, "bounced", True,
                                    reason=f"reply classified bounce ({run_id})")
        reporter.set_terminal_field(lead_note, "reply_intent", "bounce")
    else:
        reporter.set_terminal_field(lead_note, "reply_intent", intent)
    oc.append_run_log(lead_note, f"[{run_id}] reply classified: {intent} "
                                 f"(confidence={result['confidence']:.2f}, "
                                 f"ambiguous={result['ambiguous']})")
    result["lead_note"] = lead_note
    return result


# ---------------------------------------------------------------- self-test
def _selftest() -> int:
    passed = failed = 0

    def check(name, cond):
        nonlocal passed, failed
        passed += 1 if cond else 0
        failed += 0 if cond else 1
        if not cond:
            print(f"  FAIL: {name}")

    import tempfile
    import shutil

    # six mock replies, one per intent — all correctly classified
    replies = {
        "interested": "This sounds great, let's schedule a call next week.",
        "not_interested": "No thanks, we're all set for this year.",
        "objection": "We're worried about the price, it's too expensive for us right now.",
        "auto_reply": "Out of office: I am away from the office until Monday.",
        "bounce": "Undeliverable — address not found, user unknown at this domain.",
        "unsubscribe": "Please remove me from your list, unsubscribe me immediately.",
    }
    for expected, text in replies.items():
        got = classify_reply(text)
        check(f"'{expected}' reply classified as {expected}", got["intent"] == expected)
    amb = classify_reply("Thanks for the note. I'll think about it and get back to you.")
    check("deliberately ambiguous reply does not force a confident label",
          amb["ambiguous"] is True)
    check("ambiguous reply records low confidence", amb["confidence"] == 0.0)
    check("SalesGPT port: stage dictionary copied intact (7 stages)",
          len(CONVERSATION_STAGES) == 7)
    check("SalesGPT port: max-overlap stage selection runs",
          determine_conversation_stage("let's schedule a call and sign the contract")
          == "7")

    tmp = tempfile.mkdtemp(prefix="replyclass-")
    try:
        vault = oc.ensure_vault(os.path.join(tmp, "v"), template=open(
            os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..",
                                         "vault-schema", "Templates", "Lead.md")),
            encoding="utf-8").read())
        p = oc.write_lead(vault, {"lead_id": "rc-1", "name": "Un Sub", "company": "Co",
                                  "email": "u@co.io"})
        res = run(p, replies["unsubscribe"], run_id="rr-1")
        check("unsubscribe classification triggers the terminal-field write",
              oc.read_frontmatter(p)["unsubscribed"] is True)
        check("write went through the existing reporter (allowlist) — run-log line present",
              "terminal field unsubscribed=True" in oc.read_note(p))
        # a subsequent mock send attempt for that lead is blocked by the gate
        from orchestrator.policy import compliance_gate
        verdict = compliance_gate.evaluate(oc.read_frontmatter(p))
        check("subsequent mock send attempt blocked by the existing compliance gate",
              verdict["verdict"] == "block" and "not_unsubscribed" in verdict["rules"])
        p2 = oc.write_lead(vault, {"lead_id": "rc-2", "name": "B Ounce", "company": "Co",
                                   "email": "b@co.io"})
        run(p2, replies["bounce"], run_id="rr-2")
        check("bounce classification sets the bounced terminal field",
              oc.read_frontmatter(p2)["bounced"] is True)
        verdict2 = compliance_gate.evaluate(oc.read_frontmatter(p2))
        check("bounced lead is likewise blocked from further contact",
              verdict2["verdict"] == "block" and "not_bounced" in verdict2["rules"])
        p3 = oc.write_lead(vault, {"lead_id": "rc-3", "name": "In Terested",
                                   "company": "Co", "email": "i@co.io"})
        run(p3, replies["interested"], run_id="rr-3")
        fm3 = oc.read_frontmatter(p3)
        check("non-terminal intent only writes reply_intent",
              fm3["reply_intent"] == "interested" and fm3["unsubscribed"] is False)
        src = open(os.path.abspath(__file__), encoding="utf-8").read()
        from orchestrator.policy.reporter import find_terminal_field_writes
        check("classifier never bypasses the write-guard (no direct patch_frontmatter "
              "on terminal fields)", find_terminal_field_writes(src) == [])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(_selftest() if "--selftest" in sys.argv else 0)
