---
name: apersona
description: Connect Apersona and use the owner's sourced experiences and preferences for their current task. Use for Apersona setup, personal decision support, recalling the owner's experiences, or correcting their persona.
---

Use the bundled Apersona MCP connection. It supplies evidence and judgment packets; the user's model writes the answer.

## Connect and start

Discover this plugin's MCP tools. If authentication is required, use the host's connection/authentication flow for this connection and let the owner sign in and authorize. Do not add a second MCP server or request a device-code login. If the host cannot expose the installed tools yet, report that specific missing step instead of claiming connection succeeded.

Call `opening` at the start of this conversation unless its complete packet has already arrived. Retain the returned `conversation_id` and use it for subsequent calls that accept it; do not invent one or reuse another chat's identifier. Follow returned opening cursors to finish the packet. A successful tool reply, rather than installation or an OAuth redirect alone, establishes that persona data is accessible.

When the owner asks to choose or change personas/workspaces, use `workspace_status` with that identifier and `select_workspace` with an authorized choice. For a proposed switch, show the returned question and pass its original confirmation only after the owner's answer. Workspaces do not expand the OAuth grant. Do not make workspace selection a prerequisite for an already usable default persona.

## Answer the current task

Continue the task already stated. If setup was the only request, ask what current task the owner wants help with. Use `lookup` for a decision packet and `recall` for relevant original words, following the live tool schemas. Populate situation fields only with known values; model inference stays `situation_source=client_model`. Do not label an inferred situation or an industry preview as owner-confirmed.

For later recall calls in this conversation, carry the receipt identifiers of complete replies actually received in `received`, with the same conversation identifier and connection. A timeout, missing or truncated reply is not receipt confirmation. Receipt confirmation does not mean the model adopted the evidence.

Write a useful Markdown answer grounded in the returned material: options, reasons, sources, and the returned pending-item count where present. Distinguish the owner's evidence from your suggestions; missing evidence remains unknown. An empty persona or recall miss is not an authentication failure. Existing evidence can still support an answer when new reading is paused for cost.

For an owner correction, use the available correction tools against the specific claim or passage they identify. Follow their current instruction; do not infer agreement or manufacture a pending-item count. Use `source` or `delivery_history` when the task needs the original source or delivered packet.

## Additional actions

This package has no automatic hooks or conversation capture. Do not claim it automatically sends each message or records AI answers. Upload only the material the owner has requested; show the reading estimate and obtain the owner's explicit spending agreement before starting paid reading. Capture requires the owner's explicit consent. If a write is rejected as `connector_read_only`, point to the site's settings → 授權裝置與連線 to enable 上傳與修改 for this connection; do not create another login to bypass its scope.
