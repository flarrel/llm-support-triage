# Support Ticket Triage

A local HTTP service for triaging Indonesian-language support tickets about accounts, billing, workspace access, and exports. Each request returns either a draft reply with its source article and category, or a decision to send the ticket to human review. Support staff review drafts before sending them.

The service matches the ticket against a bundled knowledge base and sends the matching articles to Ollama. It validates the response against the supplied article IDs. Missing context, invalid citations, and model failures send the ticket to human review.

## Run

Use Python 3.12 and Ollama. The examples use Qwen2.5 0.5B and were tested with Ollama 0.40.16. Run these commands from the checkout in two terminals, with Ollama in the first.

```sh
mkdir -p ../runtime/models
OLLAMA_HOST=127.0.0.1:18634 OLLAMA_MODELS="$(cd ../runtime/models && pwd)" OLLAMA_NO_CLOUD=1 ollama serve
```

```sh
OLLAMA_HOST=127.0.0.1:18634 ollama pull qwen2.5:0.5b
python3 -B -m triage.service --db ../runtime/triage.sqlite3
```

The first example requests a password reset and returns a draft with the relevant article. The calendar question goes to human review because the knowledge base has no matching article. Use the run ID to match a response with its SQLite record. The health endpoint reports the pipeline version, prompt version, and model digest.

```sh
curl -fsS http://127.0.0.1:18680/health
curl -fsS http://127.0.0.1:18680/triage \
  -H 'Content-Type: application/json' \
  -d '{"ticket":"Saya lupa sandi untuk masuk."}'
curl -fsS http://127.0.0.1:18680/triage \
  -H 'Content-Type: application/json' \
  -d '{"ticket":"Apakah ada integrasi kalender?"}'
```

SQLite records version, latency, outcome, and failure reason for each run. Ticket and draft text stay out of the telemetry.

## Monitor and replay tickets

Run the tests, send the bundled synthetic tickets through the running service, and inspect the stored results.

```sh
python3 -B -m unittest discover -s tests -v
python3 -B -m triage.replay --db ../runtime/triage.sqlite3 --split holdout
python3 -B -m triage.replay --db ../runtime/triage.sqlite3
```

Replay results include correct routing, citation validity, review counts, error counts, and p95 processing latency. To compare versions, restart the service with `--mode keyword`, `--mode v1`, or `--mode v2` and replay into the same database. Use `--split diagnosis` to reproduce the cases used to investigate retrieval and citation failures.

### Replay results

The bundled data are eight support articles and 24 synthetic tickets. A supported ticket counts as correct when the service drafts a reply with the expected article and category. An out-of-scope ticket counts as correct when it routes to review without a model error.

- **Keyword baseline** — 13/15 correct and 0 validation errors. Returns article text using the top retrieval match.
- **Ollama v1** — 2/15 correct and 11 validation errors. The model put prose in the citation field, and retrieval missed receipt wording.
- **Ollama v2 (default)** — 14/15 correct and 0 validation errors. The response schema constrains citations to retrieved IDs, and retrieval recognizes “kuitansi.” All 12 generated drafts carried a retrieved citation. Local p95 processing latency was about 645 ms across the 15 requests.

The remaining miss used “bukti pembayaran” and went to review for missing context. The evaluation measures routing and citation selection on synthetic tickets. Draft wording still needs human review, and production quality and latency remain unmeasured.
