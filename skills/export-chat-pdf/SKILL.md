---
name: export-chat-pdf
description: >-
  Export a Cursor agent chat transcript to a printable PDF. Use when the user
  asks to export this chat / this conversation / this transcript to PDF, says
  导出这个chat, 导出这个对话, 导出本对话, 导出当前会话, 把这个对话导出, or 导出为 pdf,
  names $export-chat-pdf or /export-chat-pdf, or points at an
  agent-transcripts JSONL path to convert.
---

# Export Chat PDF

Convert a Cursor agent-transcript JSONL file into a PDF. Run the bundled script; do not re-implement HTML or Chrome print logic in the chat.

Script: `scripts/export_chat_pdf.py` (resolve relative to this skill directory).

## Resolve source

- **Explicit `.jsonl` path** in the user message → that file (other-chat export).
- **Otherwise this chat.** Use only the workspace `agent-transcripts/` directory named in the system prompt. Do not glob `~/.cursor/projects/*/`. Skip `subagents/`.

```bash
ls -t <agent-transcripts>/*.jsonl <agent-transcripts>/*/*.jsonl 2>/dev/null | grep -v /subagents/
```

Layouts: legacy flat (`<id>.jsonl`) and nested (`<id>/<id>.jsonl`). Read the first JSONL line of each recent candidate and match `message.content[0].text` to this conversation's opening user prompt. Use the matching path.

## Run

**This chat** (required `--strip-invocation`):

```bash
python3 ~/.cursor/skills/export-chat-pdf/scripts/export_chat_pdf.py \
  --jsonl <path> --strip-invocation --open
```

**Other chat** whose last turn is not an export request: omit `--strip-invocation` if the user wants that file verbatim. If that jsonl itself ends with an export request, pass `--strip-invocation`.

Optional: `--out PATH`, `--title TITLE`. Default PDF: `~/Downloads/<title-or-uuid>.pdf`.

**Title:** Prefer an explicit short `--title` (topic + issue, ~20–40 chars), e.g. `carizon-dt-data rebalance 反复迁移`. Do **not** leave the raw first user line when it is a shell command, path, or log dump—those make unreadable PDF headers and filenames. If omitting `--title`, the script falls back to `title_from_user_body`: prefer the ask/intent clause (分析/为什么/why/…) over a leading command prefix, then clip.

Chrome may hang after writing the PDF; the script kills that process. Do not wait on it yourself.

## Strip invocation

When exporting the chat that issued the export, the PDF must not include the skill call.

`--strip-invocation` drops **any** user turn that is only an export/skill-call (not just the last one), plus assistant records until the next user turn (the export work: path, `--open`, etc.). If the turn mixed other text with an export sentence, drop only those sentences/lines.

Phrasings the script must treat as invocation include: `export this chat`, `导出这个chat`, `导出这个对话`, `导出本对话`, `导出当前会话`, `把这个对话导出`, `导出为 pdf`, `$export-chat-pdf`, `/export-chat-pdf`. Match the **whole line** (optional 请/帮我 prefix). Do not strip a sentence that only quotes those words (e.g. review text). Do not treat unrelated 导出 (e.g. 导出 changelog) as an invocation.

Do not strip a **different** jsonl whose user turns are not export requests. The invocation lives in the current chat, not in the target.

## Q&A only (no intermediate reasoning)

Transcripts usually have **many** assistant JSONL records per user turn (tool calls, progress narration, bold “thinking” lines). The PDF must stay **user ↔ assistant one-to-one**: for each user turn, keep only the **last** non-empty assistant text before the next user message. Drop earlier assistant texts in that streak (intermediate reasoning / tool-progress). Tool-call payloads are already omitted by text extraction.

Do not concatenate all assistant messages in a turn. Do not invent a separate “reasoning” section.

## Afterward

Tell the user the PDF path. `--open` already launches it on macOS. Do not paste the transcript into the reply.
