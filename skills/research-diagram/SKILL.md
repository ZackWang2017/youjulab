---
name: research-diagram
description: Explain or revise a structured investment-research timeline or fishbone diagram.
---

The selected text is the complete JSON model of a research diagram.

For explanation requests, analyze the evidence, causal structure, gaps, and contradictions. Return `replacement: null` unless the user explicitly asks to edit the diagram.

For an edit, return the complete updated JSON object encoded as the `replacement` string. Preserve `format: invp-research-diagram-v1`, existing ids, source fields, and facts that the user did not ask to change. Do not invent dates, evidence, or sources.

Timeline schema: `{format,type:"timeline",title,events:[{id,date,title,detail?,source?}]}`.
Fishbone schema: `{format,type:"fishbone",title,problem,categories:[{id,label,items:[{id,label,evidence?,source?}]}]}`.

Use [the shared contract](../common/contracts.md).
When you need live web facts, include [external tools](../common/external-tools.md).
