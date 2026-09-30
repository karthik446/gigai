# GigAI

GigAI is a local, user-controlled agent runtime: it keeps configuration,
credentials, and work state on your machine, and makes every model call, review,
and result inspectable. **Scout**, its first Gig, finds jobs from public boards,
ranks and assesses them against your resume with the model you already use (Codex,
Claude Code, Ollama or OpenRouter), and drafts a tailored resume you can download as a PDF.

## Quickstart (Scout)

1. **Requirements.** macOS or Linux with Python 3.11+, [`uv`](https://docs.astral.sh/uv/getting-started/installation/),
   and one model CLI installed and logged in: Codex (`codex login`) or Claude Code (`claude`, then `/login`).
2. **Install.**

   ```bash
   uv tool install gigai
   ```

3. **Run.**

   ```bash
   gigai scout run      # starts Scout and opens the browser
   ```

   The setup wizard asks for your model, your resume (`.md` or `.txt`) and your
   roles; then use **Update sources**, **Run find jobs** and **Tailor resume**.
   Update later with `uv tool upgrade gigai`.

## Privacy

Scout removes your name and contact lines (email, phone, address, links) before a
model sees your resume, but it can't catch personal details elsewhere in the text,
so keep those out. The contact and Resume display fields never leave your machine.
With Ollama the resume stays on this machine. See
[Privacy and security](https://karthik446.github.io/gigai/scout/privacy/) for exactly what is sent, and to whom.

## Links

- [Documentation](https://karthik446.github.io/gigai/): quickstart, concepts, CLI and API reference, known limitations
- [Roadmap](https://karthik446.github.io/gigai/roadmap/): what is planned, without dates
- [CHANGELOG](https://github.com/karthik446/gigai/blob/main/CHANGELOG.md) and [Releases](https://github.com/karthik446/gigai/releases)
- [CONTRIBUTING](CONTRIBUTING.md) and [issues](https://github.com/karthik446/gigai/issues)

GigAI is an alpha; expect rough edges. Apache-2.0, see [LICENSE](LICENSE).
