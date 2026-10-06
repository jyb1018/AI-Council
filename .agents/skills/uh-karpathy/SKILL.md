---
name: uh-karpathy
description: Clarify consequential assumptions and constrain edits when requirements or scope are uncertain; not a mandatory planning ceremony.
---

# Karpathy-inspired engineering

This is a local, independently written adaptation, not an official Karpathy skill.
The core already provides the baseline. Use this deeper check for consequential
ambiguity, architectural choices, or a diff drifting away from the request.

Read the affected path and identify the caller, expected result, and relevant
invariant. Separate what is known from an assumption that could change the result.
Resolve low-risk details through existing conventions; ask about material product,
data ownership, security, cost, or irreversible decisions. Do not ask for facts
already present in code or the conversation.

Choose a narrow success observation. Every changed region should serve that
observation or preserve required compatibility/safety. Remove only dead code your
change created unless cleanup is requested. Name an adjacent concern rather than
silently adding it to scope. Test the behavior and inspect the resulting diff.
A plan is optional and short; full private reasoning or a formal design document
is not an output requirement. Stop this check when the decision is clear.
