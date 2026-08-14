# G26 Installed Replay

- Artifact: freshly built local `gigai-0.1.3` wheel
- Environment: disposable virtual environment under `/private/tmp`
- Provider mode: deterministic fixture only; no provider endpoint or CLI was
  contacted

The installed verifier independently confirmed:

- 29 packaged schema resources and their SHA256SUMS entries;
- valid and invalid G26 builder-session contract vectors; and
- a complete installed builder replay using a disposable GigAI home and target:
  setup, target initialization, model-built proposal draft, and explicit
  approval through the existing lifecycle.

No home directory, target repository, credentials, raw prompts, or provider
output was copied into repository evidence.
