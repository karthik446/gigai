# G16 terminal handoff

G16 implements the first deterministic, local-only Review Loop over G15
artifacts. It is ready for hosted verification and final audit.

The durable boundary is intentional: the loop does not invoke OpenAI,
OpenRouter, Codex CLI, Claude CLI, Anthropic, local models, network services,
tools, installers, or target mutation. Those effects remain G18/G17/G19 work.
