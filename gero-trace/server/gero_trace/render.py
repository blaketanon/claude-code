"""Markdown → HTML for Salesforce rich-text fields, and the engineering report / GitHub issue text."""
import html
import re

import markdown

_ALLOWED_TAGS = {"p", "br", "b", "strong", "i", "em", "u", "s", "ul", "ol", "li", "a", "h1", "h2", "h3", "h4",
                 "blockquote", "code", "pre", "table", "thead", "tbody", "tr", "th", "td", "hr", "span"}
_TAG_RE = re.compile(r"</?([a-zA-Z][a-zA-Z0-9]*)[^>]*>")
_ATTR_RE = re.compile(r"""\s([a-zA-Z-]+)\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)""")


def to_html(md_text):
    """Render markdown and keep only the tags Salesforce rich text accepts (it sanitises again on its side)."""
    if not md_text:
        return ""
    raw = markdown.markdown(md_text, extensions=["tables", "fenced_code", "sane_lists", "nl2br"])
    return _sanitize(raw)


def _sanitize(raw):
    def fix(m):
        tag = m.group(1).lower()
        if tag not in _ALLOWED_TAGS:
            return ""
        full = m.group(0)
        if full.startswith("</"):
            return f"</{tag}>"
        attrs = ""
        if tag == "a":
            for name, value in _ATTR_RE.findall(full):
                v = value.strip("\"'")
                if name.lower() == "href" and re.match(r"^(https?://|mailto:|/)", v, re.I):
                    attrs = f' href="{html.escape(v, quote=True)}" target="_blank" rel="noopener"'
        return f"<{tag}{attrs}>"
    return _TAG_RE.sub(fix, raw)


def _list(items, empty="None found."):
    items = [i for i in (items or []) if i]
    return "\n".join(f"- {i}" for i in items) if items else f"_{empty}_"


def engineering_report(inv, findings):
    """The detailed report engineers (and Claude, when fixing) read. Deterministic layout so every
    issue looks the same; the content is Claude's."""
    f = findings or {}
    fix = f.get("proposed_fix") or {}
    asked = inv.asked_by or {}
    lines = [
        f"# {f.get('title') or inv.question[:100]}",
        "",
        f"**Asked by:** {asked.get('name') or 'unknown'} ({asked.get('email') or 'no email'})  ",
        f"**Record:** {inv.record_object or '—'} {inv.record_id or ''}" + (f" — {inv.record_url}" if inv.record_url else ""),
        f"**Confidence:** {f.get('confidence') or inv.confidence or '—'}  ",
        f"**Needs a code change:** {'yes' if f.get('needs_fix') else 'no'} ({f.get('fix_type') or 'n/a'})  ",
        f"**Investigation:** `{inv.id}`" + (f" · Claude session `{inv.claude_session_id}`" if inv.claude_session_id else ""),
        "",
        "## Question",
        "",
        inv.question.strip(),
        "",
    ]
    if inv.reporter_comment:
        lines += ["## Reporter's comment", "", inv.reporter_comment.strip(), ""]
    lines += [
        "## Answer given to the user",
        "",
        (f.get("answer_markdown") or inv.answer_markdown or "").strip(),
        "",
        "## What happened (timeline)",
        "",
        (f.get("what_happened") or "").strip() or "_Not reconstructed._",
        "",
        "## Root cause",
        "",
        (f.get("root_cause") or "").strip() or "_Not determined._",
        "",
        "## Evidence",
        "",
    ]
    ev = f.get("evidence") or []
    if ev:
        for e in ev:
            if isinstance(e, dict):
                src = e.get("source") or "evidence"
                ref = e.get("reference")
                lines.append(f"- **{src}** — {e.get('detail', '')}" + (f" (`{ref}`)" if ref else ""))
            else:
                lines.append(f"- {e}")
    else:
        lines.append("_No evidence recorded._")
    lines += [
        "",
        "## Affected systems and repositories",
        "",
        _list(f.get("affected_systems")),
        "",
        "## Proposed fix",
        "",
    ]
    if fix and (fix.get("summary") or fix.get("steps")):
        lines += [
            f"**Repository:** `{fix.get('repo') or 'unknown'}`  ",
            f"**Risk:** {fix.get('risk') or 'unknown'}",
            "",
            (fix.get("summary") or "").strip(),
            "",
            "**Files likely involved**",
            "",
            _list([f"`{p}`" for p in (fix.get("files") or [])], "Not identified."),
            "",
            "**Steps**",
            "",
            "\n".join(f"{i}. {s}" for i, s in enumerate(fix.get("steps") or [], 1)) or "_None listed._",
            "",
            "**How to verify**",
            "",
            (fix.get("tests") or "").strip() or "_Not specified._",
            "",
            "**Rollback**",
            "",
            (fix.get("rollback") or "").strip() or "_Revert the pull request._",
            "",
        ]
    else:
        lines += ["_No code change proposed._", ""]
    lines += [
        "## Open questions",
        "",
        _list(f.get("open_questions"), "None."),
        "",
        "## Technical notes",
        "",
        (f.get("technical_notes") or "").strip() or "_None._",
        "",
    ]
    return "\n".join(lines).strip() + "\n"


def issue_body(inv, report_md, approve_label, public_url):
    header = [
        "> Filed from Salesforce through Gero Trace. "
        f"To let Claude implement the proposed fix, add the **`{approve_label}`** label to this issue; "
        "a draft pull request will be opened for review. Remove the label or close the issue to decline.",
    ]
    if public_url:
        header.append(f"> Investigation details: {public_url}/#/investigations/{inv.id}")
    return "\n".join(header) + "\n\n" + report_md


def issue_title(inv, findings):
    t = (findings or {}).get("title") or inv.question.strip().splitlines()[0]
    t = re.sub(r"\s+", " ", t).strip()
    return (t[:117] + "…") if len(t) > 120 else t
