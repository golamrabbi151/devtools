# DevTools Web

A DevToys-style toolbox as a web app: **Flask + HTMX**, no front-end build step.

| Tool | What it does |
|---|---|
| JSON Formatter | format / minify / validate / sort keys, syntax-coloured |
| JSON Compare | colour-coded added / removed / changed paths, optional "ignore array order" |
| SQL Formatter | pretty-print / minify, syntax-coloured (never executes SQL) |
| XML Formatter | format / minify / validate, syntax-coloured (DTD + entities rejected) |
| Cron Parser | plain-English meaning + next N runs in any timezone |
| Base64 Text | standard & URL-safe, padding options |
| Base64 Image | image → Base64 / data URI, Base64 → image preview |
| Certificate Decoder | X.509 PEM / DER / Base64-DER, bundles, fingerprints, SANs, expiry |
| QR Code | generate PNG, read from uploaded image |
| JWT | decode, verify HS256/384/512, encode + sign |
| Excel to JSON | `.xlsx` → JSON (all sheets or one), header row, hidden-sheet option; values only, formulas never evaluated |
| Lorem Ipsum | paragraphs / sentences / words, optional classic opening |
| Password Generator | CSPRNG (`secrets`), length 4–128, character sets, look-alike & custom exclusions, entropy estimate |
| UUID Generator | v4, v7, v5, v3, nil, max; formatting options; UUID inspector |

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python wsgi.py                 # http://127.0.0.1:5000 (APP_ENV=development)
pytest                         # tests, incl. security tests
bandit -r app && pip-audit     # static analysis + dependency CVEs
```

## Run in production

```bash
docker build -t devtools-web .
docker run -p 8000:8000 -e SECRET_KEY="$(python -c 'import secrets;print(secrets.token_urlsafe(48))')" devtools-web
```

Put it behind a TLS-terminating reverse proxy (nginx / Caddy / cloud LB) that sets
`X-Forwarded-Proto`. Production refuses to start without a strong `SECRET_KEY`.
With more than one worker/container, set `RATELIMIT_STORAGE_URI=redis://...` so rate limits are shared.

## Project structure

```
devtools/
├── wsgi.py                 # entry point (gunicorn wsgi:app)
├── app/
│   ├── __init__.py         # app factory, error handlers
│   ├── config.py           # Dev / Prod / Test config, size limits
│   ├── extensions.py       # CSRF, rate limiter
│   ├── security.py         # CSP + security headers
│   ├── registry.py         # Tool / Field / Action / Result + registry
│   ├── highlight.py        # safe server-side syntax highlighting (escaped spans)
│   ├── routes.py           # ONE generic page + ONE generic HTMX endpoint
│   ├── tools/              # one module per tool (logic + spec)
│   ├── templates/          # base, tool (form generated from spec), _result partial
│   └── static/             # css, app.js, vendored htmx (no CDN)
└── tests/
```

### Adding a tool

Create `app/tools/my_tool.py`, define a `handler(action, form, files) -> Result`, call
`register(Tool(...))`, and add the module name to `_MODULES` in `app/tools/__init__.py`.
The page, form, HTMX wiring, CSRF, size limits and error handling come for free.

## Upload limits and file screening

Limits live in `app/config.py` (`UPLOAD_LIMITS`) and can be overridden per deployment with env vars (in MB, hard ceiling 20):

| Tool | Default | Env var |
|---|---|---|
| Excel to JSON | **1 MB** | `UPLOAD_MB_EXCEL_JSON` |
| Base64 Image | **5 MB** | `UPLOAD_MB_BASE64_IMAGE` |
| Certificate Decoder | **2 MB** | `UPLOAD_MB_CERTIFICATE` |
| QR Code | **2 MB** | `UPLOAD_MB_QR_CODE` |
| any other file field | 2 MB | `UPLOAD_MB_DEFAULT` |

Enforced three ways: the browser warns instantly; the request body is capped *before* parsing (`413`); and the file is re-measured server-side with a friendly message.

Every upload is screened by `app/uploads.py` **before** a tool sees it. Files are never executed, never written to disk (`SafeRequest` keeps them in memory), never served back, and the client's filename/Content-Type are ignored.

- **Excel (.xlsx):** must be a real ZIP package; rejects macros (`vbaProject`, `.xlsm`, Excel-4 macro sheets), ActiveX, embedded objects, external links, custom UI, unknown/opaque parts, path-traversal names, password-protected archives, duplicate entries, DTD/entity declarations (XXE / entity bombs) and zip bombs (real decompressed size is measured with capped reads, headers are not trusted). Legacy `.xls`/OLE is refused. Parsing is read-only and takes cached values only.
- **Images (Base64 image, QR):** magic bytes must be PNG/JPEG/GIF/WebP, **nothing may follow the image data** (hidden payloads / polyglots), then Pillow fully decodes it (pixel cap against decompression bombs).
- **Certificates:** PEM or DER only; **private keys are refused** (upload and paste).
- **All:** executables (PE/ELF/Mach-O/Java), scripts, PDFs, archives, HTML/SVG/PHP are recognised and rejected with a specific reason. Stricter rate limit on Excel (10/min).

## Design

- **Theme:** buttons / bold accents `#12685d`, page `#f5f7f2`, headings `#183b35`, panels & toolbars `#e3eddb` (all CSS variables at the top of `app/static/css/app.css`).
- **Left menu** toggles from the ☰ button (state remembered per browser; on phones it is a slide-over drawer closed by the scrim or Esc).
- **JSON / XML / SQL formatters:** input and output panes side by side with line numbers, Sample / Open file / Clear, Copy / Download, colour-coded output.
- **JSON Compare:** two editors with the Compare button between them and a side-by-side Left / Right difference table with added / removed / changed badges.
- **Cron:** large expression box that re-parses as you type, example chips, per-field breakdown cards and a "next runs" table with relative times.

## Capacity and sizing

Measured on 2 shared vCPU (load generator on the same box), gunicorn, one process per core:

| Request type | CPU time |
|---|---|
| Page view, small JSON/UUID/cron | 3-7 ms |
| Password x20, QR read | 10-15 ms |
| 190 KB JSON / SQL format, QR generate | 35-65 ms |
| JSON compare 2 x 190 KB | ~85 ms |
| Base64 image 1 MB / 4.3 MB | ~125 ms / ~400 ms |
| Excel to JSON, 1k rows / 8k rows | ~0.3 s / ~0.65 s (worst-case dense 1 MB file: ~2.6 s) |

- **Throughput:** about 90 requests/s per core for a realistic mix, about 35-40 requests/s per core for a deliberately heavy mix (5 % Excel). At one request per user every ~15 s that is roughly **500-1,300 simultaneously active users per core**.
- **Overload behaviour:** requests queue and slow down (seconds), they do not fail: 150 simultaneous users on 2 cores gave 0 errors and no worker restarts. Rate limits (429) and `--timeout` (stuck request killed after 30 s) protect the rest.
- **Memory:** ~80 MB per idle worker, ~110-150 MB after heavy uploads (worst case a 20-megapixel image can briefly need ~250 MB).

Production settings (`gunicorn.conf.py`, all env-overridable): `gthread` workers (stalled clients freeze plain sync workers), `WEB_CONCURRENCY` = CPU cores, `WEB_THREADS=4`, workers recycled every ~1000 requests. Set `PROXY_HOPS=1` behind nginx / a load balancer, otherwise every visitor shares the proxy's IP and one rate-limit bucket. `GET /healthz` is the health probe. `EXCEL_MAX_PARALLEL` (default 2) bounds concurrent Excel conversions per process.

| Expected active users | Book | Notes |
|---|---|---|
| up to ~500 (team, small public) | 2 vCPU, 2 GB RAM | 1 instance |
| ~500-2,500 | 4 vCPU, 4 GB RAM | 1 instance, `WEB_CONCURRENCY=4` |
| more / high availability | 2-3 instances x (2 vCPU, 2 GB) behind a load balancer | add Redis: `RATELIMIT_STORAGE_URI=redis://...` |

Always put nginx / a CDN in front (TLS, static files, request buffering). Disk needs are tiny: nothing is stored. Re-measure on your own hardware with `scripts/loadtest.py`.

## Security design

- **No inline JS/CSS, no CDN**: CSP is `default-src 'none'; script-src 'self'; style-src 'self'` with no `unsafe-inline`/`unsafe-eval`. HTMX is vendored and configured with `allowEval=false`, `allowScriptTags=false`, `selfRequestsOnly=true`.
- **UI scripts** (`app.js`) never use `innerHTML`; files you open are read locally in the browser; the menu preference is stored in `localStorage` inside try/catch and contains no user data. Icons are inline SVG and the favicon is a `data:` URI, so the CSP needs no relaxation.
- **CSRF** on every POST (Flask-WTF); `SameSite=Strict` secure cookies.
- **XSS**: Jinja autoescaping everywhere, no `|safe`. Syntax colouring is generated server-side: every token is escaped, wrapped in a `<span>` with a fixed class name, and returned as `Markup`; colours live in the stylesheet, so the CSP stays strict. Output over 300k characters falls back to plain text. Images shown to the user are re-encoded by Pillow into PNG data URIs that we generate; uploaded bytes are never served back.
- **XML**: `defusedxml` with DTDs/entities/external refs forbidden (blocks XXE and entity bombs).
- **Resource limits**: 3 MB request, 500k chars per text field, 2 MB per upload, 20 MP image cap (decompression bombs), 200k chars for SQL, QR text ≤ 2000 chars, recursion errors handled.
- **Rate limiting** (Flask-Limiter): 30 runs/min and 120 requests/min per IP by default.
- **Input handling**: only declared fields are read; actions are allow-listed; user input is never executed, never written to disk and never logged (errors log only the exception type).
- **JWT**: `alg: none` is flagged as unsigned and never "valid"; signature comparison is constant-time.
- **Headers**: HSTS (when HTTPS), `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, COOP/CORP, `Cache-Control: no-store`.
- **Container**: non-root user, slim image, Werkzeug debugger never enabled.

## Known limits / next steps

- JWT signature verification covers HS256/384/512 only (RS/ES need a public-key field).
- Rate limiting is per-IP; behind a proxy configure `ProxyFix` with the correct hop count.
- Don't paste real production secrets, tokens or private keys into any hosted tool.
