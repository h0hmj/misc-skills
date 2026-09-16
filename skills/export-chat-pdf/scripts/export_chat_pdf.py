#!/usr/bin/env python3
"""Convert a Cursor agent-transcript JSONL file to a printable PDF."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Whole-line matchers for "export this chat" (EN + ZH). Must be fullmatch:
# quoting the phrase inside another sentence is not an invocation.
# English tokens need \b so 导出changelog does not match; CJK must not
# use \b (话/导 are both \w).
_CHAT = r"(?:对话|会话|(?:chat|conversation|transcript)\b)"
_LEAD = r"(?:请|麻烦|帮我|帮忙)?\s*"
_TAIL = r"\s*(?:吧|谢谢|thanks)?[。.!！?？]*"


def _inv(pat: str) -> re.Pattern[str]:
    return re.compile(rf"^{_LEAD}(?:{pat}){_TAIL}$", re.I)


INVOCATION_RES = [
    _inv(r"\$export-chat-pdf"),
    _inv(r"/export-chat-pdf"),
    _inv(r"export-chat-pdf"),
    _inv(
        r"export\s+(?:the\s+)?(?:current\s+)?(?:this\s+)?"
        r"(?:chat|conversation|transcript)\b(?:\s+to\s+pdf)?"
    ),
    _inv(r"export\b.+\bto\s+pdf\b"),
    _inv(r"export\b.+\.jsonl\b.+\bpdf\b"),
    _inv(rf"导出\s*(?:一?下\s*)?(?:本|这个|此|该|当前)?\s*{_CHAT}"),
    _inv(rf"把\s*(?:本|这个|此|该|当前)?\s*{_CHAT}\s*导出"),
    _inv(r"导出为\s*pdf"),
    _inv(r"导出.{0,40}pdf"),
]

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
]


def extract_text(obj: dict) -> str:
    content = obj.get("message", {}).get("content", [])
    parts = []
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                parts.append(c.get("text") or "")
            elif isinstance(c, str):
                parts.append(c)
    return "\n".join(parts)


def user_query_and_ts(text: str) -> tuple[str, str]:
    ts = ""
    m = re.search(r"<timestamp>(.*?)</timestamp>", text, re.S)
    if m:
        ts = m.group(1).strip()
        text = text[: m.start()] + text[m.end() :]
    m = re.search(r"<user_query>\s*(.*?)\s*</user_query>", text, re.S)
    if m:
        text = m.group(1)
    text = re.sub(r"</?user_query>", "", text)
    return ts, text.strip()


def is_invocation_line(line: str) -> bool:
    s = line.strip().strip("\"'“”「」")
    if not s:
        return False
    return any(rx.fullmatch(s) for rx in INVOCATION_RES)


def is_invocation_text(text: str) -> bool:
    body = user_query_and_ts(text)[1]
    if not body:
        return False
    lines = [ln for ln in body.splitlines() if ln.strip()]
    if not lines:
        return False
    return all(is_invocation_line(ln) for ln in lines)


def strip_invocation_from_text(text: str) -> str | None:
    """Return rewritten user text, or None if the whole turn should be dropped."""
    ts = ""
    m = re.search(r"<timestamp>(.*?)</timestamp>", text, re.S)
    if m:
        ts = m.group(1).strip()
    _, body = user_query_and_ts(text)
    kept = [ln for ln in body.splitlines() if not is_invocation_line(ln)]
    kept_body = "\n".join(kept).strip()
    if not kept_body:
        return None
    if ts:
        return f"<timestamp>{ts}</timestamp>\n<user_query>\n{kept_body}\n</user_query>"
    return kept_body


def load_records(path: Path) -> list[dict]:
    records = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def _rewrite_user_record(rec: dict, rewritten: str) -> dict:
    msg = dict(rec)
    inner = dict(msg.get("message") or {})
    content = inner.get("content")
    if isinstance(content, list):
        new_content = []
        replaced = False
        for c in content:
            if (
                not replaced
                and isinstance(c, dict)
                and c.get("type") == "text"
            ):
                new_content.append({**c, "text": rewritten})
                replaced = True
            else:
                new_content.append(c)
        inner["content"] = new_content
    else:
        inner["content"] = rewritten
    msg["message"] = inner
    return msg


def _skip_until_next_user(records: list[dict], i: int) -> int:
    """Advance past rec i and any following non-user records (assistant export work)."""
    i += 1
    while i < len(records) and records[i].get("role") != "user":
        i += 1
    return i


def strip_invocation(records: list[dict]) -> list[dict]:
    """Drop export/skill-call user turns anywhere, not only at the end.

    A turn that is *only* an invocation is removed, along with assistant
    records until the next user turn. Mixed turns keep non-invocation lines.
    """
    out: list[dict] = []
    i = 0
    n = len(records)
    while i < n:
        rec = records[i]
        if rec.get("role") != "user":
            out.append(rec)
            i += 1
            continue
        text = extract_text(rec)
        if is_invocation_text(text):
            i = _skip_until_next_user(records, i)
            continue
        rewritten = strip_invocation_from_text(text)
        if rewritten is None:
            i = _skip_until_next_user(records, i)
            continue
        _, body = user_query_and_ts(text)
        _, new_body = user_query_and_ts(rewritten)
        if new_body != body:
            rec = _rewrite_user_record(rec, rewritten)
        out.append(rec)
        i += 1
    return out


def md_inline(s: str) -> str:
    s = html.escape(s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", s)
    return s


def convert_md(text: str) -> str:
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        m = re.match(r"^```(.*)$", line)
        if m:
            info = m.group(1).strip()
            i += 1
            code = []
            while i < n and not lines[i].startswith("```"):
                code.append(lines[i])
                i += 1
            if i < n:
                i += 1
            caption = ""
            lang = html.escape(info)
            if info and re.match(r"^\d+:\d+:", info):
                caption = f'<div class="code-caption">{html.escape(info)}</div>'
                lang = ""
            out.append(
                caption
                + f'<pre><code class="{lang}">'
                + html.escape("\n".join(code))
                + "</code></pre>"
            )
            continue
        if "|" in line and i + 1 < n and re.match(
            r"^\s*\|?\s*[-:]+(\s*\|\s*[-:]+)+\s*\|?\s*$", lines[i + 1]
        ):
            rows = []
            while i < n and "|" in lines[i] and lines[i].strip():
                if re.match(
                    r"^\s*\|?\s*[-:]+(\s*\|\s*[-:]+)+\s*\|?\s*$", lines[i]
                ):
                    i += 1
                    continue
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                rows.append(cells)
                i += 1
            if rows:
                thead = (
                    "<tr>"
                    + "".join(f"<th>{md_inline(c)}</th>" for c in rows[0])
                    + "</tr>"
                )
                tbody = ""
                for r in rows[1:]:
                    tbody += (
                        "<tr>"
                        + "".join(f"<td>{md_inline(c)}</td>" for c in r)
                        + "</tr>"
                    )
                out.append(
                    f"<table><thead>{thead}</thead><tbody>{tbody}</tbody></table>"
                )
            continue
        if re.match(r"^\s*---+\s*$", line):
            out.append("<hr>")
            i += 1
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{md_inline(m.group(2))}</h{lvl}>")
            i += 1
            continue
        if re.match(r"^[-*]\s+", line):
            items = []
            while i < n and re.match(r"^[-*]\s+", lines[i]):
                items.append(
                    "<li>"
                    + md_inline(re.sub(r"^[-*]\s+", "", lines[i]))
                    + "</li>"
                )
                i += 1
            out.append("<ul>" + "".join(items) + "</ul>")
            continue
        if re.match(r"^\d+\.\s+", line):
            items = []
            while i < n and re.match(r"^\d+\.\s+", lines[i]):
                items.append(
                    "<li>"
                    + md_inline(re.sub(r"^\d+\.\s+", "", lines[i]))
                    + "</li>"
                )
                i += 1
            out.append("<ol>" + "".join(items) + "</ol>")
            continue
        if not line.strip():
            i += 1
            continue
        paras = [line]
        i += 1
        while (
            i < n
            and lines[i].strip()
            and not re.match(
                r"^(#{1,6}\s|```|[-*]\s|\d+\.\s|---+\s*$)", lines[i]
            )
            and "|" not in lines[i]
        ):
            paras.append(lines[i])
            i += 1
        out.append("<p>" + md_inline(" ".join(paras)) + "</p>")
    return "\n".join(out)


def records_to_turns(records: list[dict]) -> list[tuple[str, str, str]]:
    """Build user/assistant Q&A turns.

    Cursor transcripts emit many assistant records per user turn (tool
    progress / intermediate reasoning). Keep only the *last* non-empty
    assistant text before the next user message so the PDF is one reply
    per question.
    """
    turns: list[tuple[str, str, str]] = []
    pending_assistant: tuple[str, str, str] | None = None

    def flush_assistant() -> None:
        nonlocal pending_assistant
        if pending_assistant is not None:
            turns.append(pending_assistant)
            pending_assistant = None

    for obj in records:
        if obj.get("type") == "turn_ended" or "role" not in obj:
            continue
        role = obj["role"]
        text = extract_text(obj)
        if role == "user":
            flush_assistant()
            ts, body = user_query_and_ts(text)
            if body:
                turns.append(("user", ts, body))
        elif role == "assistant":
            body = text.replace("[REDACTED]", "")
            body = re.sub(r"\n{3,}", "\n\n", body).strip()
            if body:
                # Overwrite: earlier messages in the streak are reasoning.
                pending_assistant = ("assistant", "", body)
    flush_assistant()
    return turns


CSS = """
@page { size: A4; margin: 18mm 16mm 20mm 16mm; }
* { box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB",
    "Noto Sans CJK SC", "Source Han Sans SC", "Microsoft YaHei", sans-serif;
  font-size: 11pt;
  line-height: 1.55;
  color: #1a1a1a;
  max-width: 100%;
}
.header {
  border-bottom: 2px solid #1f6feb;
  padding-bottom: 12px;
  margin-bottom: 22px;
}
.header h1 { font-size: 18pt; margin: 0 0 6px 0; color: #0d1117; }
.meta { color: #57606a; font-size: 9.5pt; }
.turn { page-break-inside: avoid; margin: 0 0 22px 0; }
.turn.assistant-long { page-break-inside: auto; }
.badge {
  display: inline-block;
  font-size: 9pt;
  font-weight: 600;
  letter-spacing: .02em;
  padding: 2px 8px;
  border-radius: 4px;
  margin-bottom: 8px;
}
.user .badge { background: #ddf4ff; color: #0969da; }
.assistant .badge { background: #dafbe1; color: #1a7f37; }
.ts { color: #8c959f; font-size: 9pt; margin-left: 8px; }
h2 { font-size: 14pt; margin: 1.1em 0 .4em; color: #0d1117; }
h3 { font-size: 12.5pt; margin: 1em 0 .35em; color: #24292f; }
h4 { font-size: 11.5pt; margin: .9em 0 .3em; }
p { margin: .45em 0; }
ul, ol { margin: .4em 0 .6em 1.3em; padding: 0; }
li { margin: .2em 0; }
hr { border: none; border-top: 1px solid #d0d7de; margin: 1.2em 0; }
code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 9.2pt;
  background: #f6f8fa;
  padding: 1px 4px;
  border-radius: 3px;
}
pre {
  background: #f6f8fa;
  border: 1px solid #d0d7de;
  border-radius: 6px;
  padding: 10px 12px;
  overflow-x: auto;
  font-size: 8.6pt;
  line-height: 1.4;
  white-space: pre-wrap;
  word-break: break-all;
  page-break-inside: avoid;
}
pre code { background: none; padding: 0; font-size: inherit; }
.code-caption {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 8pt;
  color: #57606a;
  margin: 8px 0 -6px;
}
table {
  border-collapse: collapse;
  width: 100%;
  margin: .7em 0 1em;
  font-size: 9.8pt;
  page-break-inside: avoid;
}
th, td {
  border: 1px solid #d0d7de;
  padding: 6px 8px;
  text-align: left;
  vertical-align: top;
}
th { background: #f6f8fa; font-weight: 600; }
.footer-note { color: #8c959f; font-size: 8.5pt; margin-top: 28px; border-top: 1px solid #d0d7de; padding-top: 8px; }
"""


def slug(s: str, max_len: int = 60) -> str:
    s = re.sub(r"\s+", "-", s.strip())
    s = re.sub(r"[^\w\u4e00-\u9fff.-]+", "", s, flags=re.U)
    s = s.strip("-.") or "chat"
    return s[:max_len]


def render_html(turns: list[tuple[str, str, str]], title: str, source: str) -> str:
    parts = []
    for role, ts, body in turns:
        cls = "turn " + role
        if role == "assistant" and len(body) > 800:
            cls += " assistant-long"
        label = "User" if role == "user" else "Assistant"
        ts_html = f'<span class="ts">{html.escape(ts)}</span>' if ts else ""
        body_html = (
            convert_md(body) if role == "assistant" else f"<p>{md_inline(body)}</p>"
        )
        parts.append(
            f'<section class="{cls}"><div class="badge">{label}</div>'
            f'{ts_html}<div class="content">{body_html}</div></section>'
        )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>{CSS}</style>
</head>
<body>
<header class="header">
  <h1>{html.escape(title)}</h1>
  <div class="meta">{html.escape(source)}</div>
</header>
{''.join(parts)}
<p class="footer-note">Exported from Cursor agent transcript. Intermediate reasoning and tool-call internals omitted; last assistant reply per turn only.</p>
</body>
</html>
"""


def find_chrome() -> str:
    for p in CHROME_CANDIDATES:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    raise SystemExit(
        "No Chrome/Edge/Chromium found under /Applications. Cannot print PDF."
    )


def print_pdf(html_path: Path, pdf_path: Path, timeout: int = 20) -> None:
    chrome = find_chrome()
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    if pdf_path.exists():
        pdf_path.unlink()
    udd = Path(tempfile.mkdtemp(prefix="export-chat-pdf-"))
    cmd = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
        f"--user-data-dir={udd}",
        "--no-pdf-header-footer",
        f"--print-to-pdf={pdf_path}",
        html_path.resolve().as_uri(),
    ]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    deadline = time.time() + timeout
    try:
        while time.time() < deadline:
            if pdf_path.exists() and pdf_path.stat().st_size > 0:
                time.sleep(0.4)
                break
            if proc.poll() is not None:
                break
            time.sleep(0.2)
        else:
            pass
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        shutil.rmtree(udd, ignore_errors=True)
    if not pdf_path.exists() or pdf_path.stat().st_size == 0:
        raise SystemExit(f"PDF was not written: {pdf_path}")


def _looks_like_command(s: str) -> bool:
    s = s.strip()
    if not s:
        return False
    if s.startswith(("/", "~/", "./")):
        return True
    if re.match(r"^(sudo\s+)?(/\S+/)?juicefs\b", s, re.I):
        return True
    if ">>" in s or " 2>" in s or re.search(r"\s\|\s", s[:100]):
        return True
    return False


def _looks_like_request(s: str) -> bool:
    return bool(
        re.search(
            r"(分析|为什么|为何|怎么|如何|排查|调查|解释|说明|"
            r"\bwhy\b|\bhow\b|\bwhat\b|analyze|investigate|explain)",
            s,
            re.I,
        )
    )


def _clip_title(s: str, max_len: int = 60) -> str:
    s = re.sub(r"\s+", " ", s).strip().rstrip("。.!！?？")
    if len(s) <= max_len:
        return s
    cut = s[:max_len]
    for ch in (" ", "，", "、", ",", "：", ":"):
        i = cut.rfind(ch)
        if i >= 16:
            return cut[:i].rstrip()
    return cut.rstrip()


def title_from_user_body(body: str) -> str:
    """Prefer the ask/intent clause over a leading shell/path dump."""
    text = re.sub(r"\s+", " ", body.strip())
    if not text:
        return ""
    parts = [
        p.strip()
        for p in re.split(r"[，。；;]|,\s+|\.\s+", text)
        if p.strip()
    ]
    for p in parts:
        if _looks_like_request(p) and not _looks_like_command(p):
            return _clip_title(p)
    if parts and _looks_like_command(parts[0]) and len(parts) > 1:
        rest = "，".join(parts[1:])
        return _clip_title(rest)
    if _looks_like_command(text):
        m = re.search(r"\bjuicefs\s+(\w+)\s+([\w.-]+)", text, re.I)
        if m:
            return _clip_title(f"juicefs {m.group(1)} {m.group(2)}")
    return _clip_title(text)


def default_title(turns: list[tuple[str, str, str]], jsonl: Path) -> str:
    for role, _, body in turns:
        if role == "user" and body:
            title = title_from_user_body(body)
            if title:
                return title
    return jsonl.stem


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jsonl", required=True, type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--title")
    ap.add_argument("--strip-invocation", action="store_true")
    ap.add_argument("--open", action="store_true", dest="open_pdf")
    args = ap.parse_args()

    jsonl = args.jsonl.expanduser().resolve()
    if not jsonl.is_file():
        print(f"jsonl not found: {jsonl}", file=sys.stderr)
        return 1

    records = load_records(jsonl)
    if args.strip_invocation:
        records = strip_invocation(records)
    turns = records_to_turns(records)
    if not turns:
        print("no user/assistant text turns after filtering", file=sys.stderr)
        return 1

    title = args.title or default_title(turns, jsonl)
    downloads = Path.home() / "Downloads"
    out = args.out.expanduser() if args.out else downloads / f"{slug(title)}.pdf"
    if out.suffix.lower() != ".pdf":
        out = out.with_suffix(".pdf")

    html_doc = render_html(turns, title, str(jsonl))
    with tempfile.NamedTemporaryFile(
        "w", suffix=".html", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(html_doc)
        html_path = Path(tmp.name)
    try:
        print_pdf(html_path, out)
    finally:
        html_path.unlink(missing_ok=True)

    print(str(out))
    if args.open_pdf:
        subprocess.run(["open", str(out)], check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
