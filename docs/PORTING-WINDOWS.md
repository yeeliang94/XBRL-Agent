# Porting Checklist — Mac → Windows

`start.bat` handles almost everything automatically. This is the manual
checklist when something goes wrong.

1. `git push` from Mac, `git pull` on Windows.
2. Run `start.bat` (double-click or `.\start.bat`). It auto-discovers Python
   + Node.js and sets `PYTHONUTF8=1`.
3. **First run:** Notepad opens `.env` — fill in `GOOGLE_API_KEY` from Bruno
   (Collection → Auth tab).
4. Set `LLM_PROXY_URL=https://genai-sharedservice-emea.pwc.com` (enterprise
   proxy).
5. Verify the proxy is reachable — must be on corporate network / VPN. Direct
   Google API calls are blocked (403). The proxy presents a corporate-MITM
   cert; `server.py` injects `truststore` at startup so Python trusts the
   Windows cert store. If you see `httpx.ConnectError: [SSL:
   CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate`,
   `pip install -r requirements.txt` to pick up `truststore` (Python ≥ 3.10).
6. Confirm `PYTHONUTF8=1` is set in the environment (start.bat does this; if
   running manually: `set PYTHONUTF8=1 && python server.py`).
7. If pydantic-ai version differs from Mac: check `_create_proxy_model()` in
   `server.py`. See CLAUDE.md gotcha #2 for the 1.77+ API.
8. If Node.js isn't on PATH: `start.bat` looks in `C:\Program Files\nodejs\`.
   If it's elsewhere, set PATH manually before running.

**Note:** On Windows, `start.bat` does **not** launch a local LiteLLM proxy —
it uses the enterprise proxy directly. Only `start.sh` (Mac) runs a local
LiteLLM instance on port 4000.

## Reference

- `start.bat` — Windows entrypoint
- `start.sh` — Mac/Linux entrypoint (local LiteLLM on `:4000`)
- `litellm_config.yaml` — local proxy config (Mac only)
- `.env.example` — env template
- CLAUDE.md gotchas #1 (PYTHONUTF8), #2 (pydantic-ai), #5 (SSL warning), #8
  (Node PATH)
