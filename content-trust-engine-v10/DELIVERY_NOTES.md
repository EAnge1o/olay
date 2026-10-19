# Delivery notes

## What is included

- Installable `content_trust` Python package and versioned prompts.
- Python API for the engine and four-role agent.
- Setup template, usage documentation, evaluation fixtures, tests, and helper scripts.
- The small local post sample used by `quick_start.py`.
- `demo/agent-run-report-v10.json`, the complete structured output from the latest text + image run.

## Start

Use Python 3.11 or newer. From this folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
cp .env.example .env
```

Set `QWEN_API_KEY` for image semantic analysis and `DEEPSEEK_API_KEY` for text analysis and report explanation. Then run:

```bash
python quick_start.py
```

For integration, import `ContentTrustAgent` and `AgentInput` as shown in `README.md`. The response is JSON-serializable via `result.model_dump(mode="json")`.

## Reference run

- Overall score: 29.62/100, low triage risk.
- Text: 55/100, medium wording/evidence risk; 12 claims were marked insufficient evidence because no supporting evidence was supplied.
- Image: Qwen semantic comparison returned `MATCH`; this is not a pixel-level forensic result.
- AI-style signal: 0.35/1.00 with confidence 0.30. It is uncalibrated and is not an authorship probability.
- Evidence chain: 42 items (19 text, 23 image).
- Provider errors: none; elapsed time about 49.6 seconds.

The report contains no comments or account history, so those DeepSeek roles are explicitly `skipped`. No independent image-forensics model was run. The raw Qwen summary includes one unsupported sentence about there being no obvious AI-generation/local-manipulation traces; do not present that phrase as forensic evidence. The report's structured fields and limitations correctly mark pixel-level forensics unavailable.

## Excluded from this bundle

The local `.env`, virtual environment, Git metadata, model checkpoints, full research/evaluation image datasets, and raw provider-debug dumps are excluded. Add credentials to your own `.env`; never put API keys into the package or reports.
