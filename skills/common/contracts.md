# Office selection contract

The browser owns the live document. A context snapshot identifies document_id,
surface, revision, selection, and surrounding text. Sheet targets carry sheet_id,
zero-based row/column, rows/cols, and values. Document targets carry ProseMirror
from/to positions, text, and single_block. PDF targets carry 1-based page, text,
filename, and PDF-coordinate boxes. Mindmap targets carry node `id`, topic
`text`, `path` (root → node), `parent_id`, and `is_root`. Research-diagram
targets carry the complete validated timeline or fishbone JSON in `text` and
the current `diagram_type`.

The runtime creates patches from validated replacements and optional sheet
`format_ops`; the model cannot pick another target. 选区解释 edits replace the
selected range (or node topic on mindmap). 生成内容 inserts at the caret, active
cell, or as a child of the selected mindmap node. Document inserts accept
Markdown (headings, bold, italic, lists) and convert it to editor rich text.
Sheet inserts may grow a matrix from the cursor. Sheet format-only edits use
`format_ops` with A1 `address` and Excel-like `number_format` (e.g. `accounting`
→ `#,##0.00`, or `0.00%`) while `replacement` stays null. Research-diagram edits
replace the complete structured JSON model. A patch contains document_id,
surface, revision, selection, replacement, optional format_ops, and patch_id.
Validate again against the current snapshot before applying. Changed document
revisions reject a patch. Accept or discard is a user action. MVP snapshots are
ephemeral; editor JSON is saved in browser storage.

Unless the user writes in another language, respond in **简体中文**. If quoted or
referenced material is 繁体中文, convert the explanation and answer to 简体中文
(keep official names / codes unchanged when appropriate).
