---
name: research-diagram-generate
description: Generate a structured investment-research timeline or fishbone diagram.
---

Generate a complete research diagram matching the user's intent and return it as a JSON-encoded `replacement` string.

Use `format: invp-research-diagram-v1`. Choose `type: timeline` for chronological events, catalysts, policy changes, financing, earnings, or product milestones. Choose `type: fishbone` for attribution, risks, thesis validation, and causal decomposition.

Timeline schema: `{format,type:"timeline",title,events:[{id,date,title,detail?,source?}]}`.
Fishbone schema: `{format,type:"fishbone",title,problem,categories:[{id,label,items:[{id,label,evidence?,source?}]}]}`.

Generate stable unique ids using short descriptive ASCII strings. Keep labels concise. Never invent dates, facts, evidence, or sources; leave uncertain fields empty and explain what evidence is missing in `answer`.

Use [the shared contract](../common/contracts.md).
When you need live web facts, include [external tools](../common/external-tools.md).
