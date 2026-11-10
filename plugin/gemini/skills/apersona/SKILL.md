---
name: apersona
description: Use Apersona to connect a persona, recall experiences, apply preferences to decisions, record owner corrections, upload material and confirm or decline reading, or turn the integration on or off.
---

Use the extension's `apersona` MCP server. If it needs authentication, have the owner run `/mcp auth apersona` inside Gemini CLI and complete browser authorization. Do not add another MCP server or request an API key. If the tools are absent, check `/extensions list` and `/mcp`; a settings.json server with the same name overrides the extension connection.

Call `opening` once for this chat and follow any returned cursor until complete. Keep its returned `conversation_id` for later calls; never invent an identifier. Installation and OAuth alone do not prove access: confirm a successful opening reply. An empty persona is not an authentication error.

Continue the owner's actual task. Use `lookup` for judgment and `recall` for sourced original words, following the live schemas. Separate source evidence from model inference (`situation_source=client_model`). If asked to change workspaces, use `workspace_status` and `select_workspace`; obtain the owner's answer before confirming a proposed switch. A usable default persona needs no workspace setup.

On later recall calls, return only receipt identifiers from complete replies actually received through this same connection in `received`, retaining the conversation identifier. Missing, truncated or timed-out replies are not acknowledged. Receipt is not evidence of adoption.

Answer with sources, reasons and uncertainties. Missing evidence remains unknown. These packages do not automatically capture messages or AI answers. Capture requires explicit owner consent; paid reading requires the owner's agreement to its estimate. A `connector_read_only` rejection means the owner must enable uploads and modifications for this connection in the site's settings; do not create another login to bypass it.

When the owner corrects a judgment ("that is not me" or "I would not do that"), discover `claim` and call it with `kind="correction"`, their own words in `text`, and the applicable `stake` from the live schema. Include only context they supplied; do not invent a registration identifier or correction reason. To withdraw a correction at their request, use its returned number in `withdraws` and their withdrawal words in `text`. Check the tool reply before saying the correction was saved. Owner corrections remain available on read-only connections.

When asked to upload material, discover `upload_files` and send only the requested files, each with `name` and `text` or `base64`, following its live schema. Uploading prepares material; it does not authorize reading. Show the returned file results, estimate or free allowance, and exact consent question. If needed, retrieve them with `upload_result(job_id=...)`. Wait for the owner's answer, including when reading is free. If they agree, call `agree_upload` with the returned `job_id`, `consent_sha256`, and their exact words as `owner_reply`; if they refuse, call `decline_upload` with `job_id` and their exact `owner_reply`. Silence is neither consent nor refusal. Use `job` to check agreed reading progress. Declining reading does not delete the uploaded originals.

To turn this integration off or on, use Gemini native extension management: `gemini extensions disable apersona` or `gemini extensions enable apersona`; remove it with `gemini extensions uninstall apersona`. These controls do not enable automatic capture or revoke OAuth. Revoke authorization separately in the site's authorized connections settings. Do not use Claude `/apersona:on` or `/apersona:off` commands here.
