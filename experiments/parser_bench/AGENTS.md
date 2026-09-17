# Parser benchmark

The root AGENTS.md applies. This is an isolated, local PDF experiment, not the
application's canonical extraction path or its eval subsystem.

- Use this folder's CLI and `.venvs/<engine>` interpreters for parser commands.
  The root repository interpreter may bootstrap the CLI and run its unit tests.
- Never add parser packages to the application's requirements or import application
  modules. Do not modify the application, database, prompts, or templates.
- Documents, extracted content, model weights, logs, and reference answers stay
  local and ignored by Git. Do not upload documents or enable cloud fallbacks.
- Inspect setup/probe failures before changing versions. Preserve original logs.
  Never disable TLS verification or bypass enterprise policy.
- Model initialization can download weights. Obtain the user's authorization for
  downloads on the target machine before using `--allow-model-init`. Offline status
  is unverified unless network blocking is independently established.
- A successful import is not successful extraction. Empty, missing, duplicate,
  failed, or truncated pages must remain visible in results.
- Only human-verified gold with the exact document SHA-256 can be scored. Draft
  annotations are not verified. Do not turn parser output into its own gold.
- Use synthetic fixtures for tracked tests. Working plans/handoffs belong under
  the repository's docs/local; maintained operating instructions belong in README.
- Run `venv/Scripts/python.exe -m pytest experiments/parser_bench/tests -q`
  on Windows (root venv/bin/python elsewhere). Run smoke tests only for installed
  engines and report all engines not exercised.
