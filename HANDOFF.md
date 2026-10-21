# MASARI — Handoff

_Last updated: 2026-10-21 · Branch: `main` (clean, last commit `c57dbab`)_

## What this is
MASARI is a career and learning app for computer science students. It offers:
- course recommendations and AI study plans
- mock interviews: technical questions, behavioral questions answered by voice and scored with the STAR method, quizzes and cheatsheets
- an ATS résumé optimizer, which is the paid product

> The README says the stack is Flask + PyMongo. That is out of date. The real stack is **FastAPI + Motor (async MongoDB) + Jinja2**.

## Run it
```bash
pip install -r requirements.txt
uvicorn main:app --reload --port 5000    # or: docker compose up
```
Environment variables (`.env`): `MONGO_URI`, `SECRET_KEY`, `GEMINI_API_KEY`, the Groq API key, and the Google OAuth credentials for `/login/google`.
Deployment: Vercel ([vercel.json](vercel.json)) or Docker. On Vercel, FastAPI's startup hook doesn't run, so [main.py](main.py) also sets up Mongo and the ML models when the module is imported.

## Code map
| Path | Purpose |
|---|---|
| [main.py](main.py) | App setup: Mongo and its indexes, NLTK downloads, middleware (sessions, proxy headers, slowapi rate limiting), router registration |
| [dependencies.py](dependencies.py) | `get_db`, `get_current_user`, `limiter` |
| [core_templates.py](core_templates.py) | `is_premium`, `check_is_super_admin` |
| [ai_core.py](ai_core.py) | TF-IDF / cosine-similarity course search (cache: `datasets/tfidf_cache.pkl`) |
| [interview_analyzer/](interview_analyzer/) | "phase2" pipeline: EDA, BM25 retrieval, recommendations, evaluation, plus question data in `storage/` |
| [routers/pages.py](routers/pages.py) | Public pages, home page, `/search` |
| [routers/auth.py](routers/auth.py) | Register, login, Google OAuth, password reset, onboarding, profile |
| [routers/course.py](routers/course.py) | Course analyze, compare, submit, link validation |
| [routers/dashboard.py](routers/dashboard.py) | Stats, `/generate_path` study plans, saving paths and progress, chat assistant |
| [routers/interview.py](routers/interview.py) | Interview question search, follow-ups, technical questions, Whisper transcription, STAR scoring, quizzes, cheatsheets |
| [routers/quiz_bank.py](routers/quiz_bank.py) | Local quiz questions used when the AI call fails |
| [routers/resume.py](routers/resume.py) | InstaPay checkout and `/api/resume/analyze` (uses credits, limited to 3 requests per minute) |
| [routers/admin.py](routers/admin.py) | Course approval and CRUD, user role and premium toggles, scraper, CSV upload |
| [scraper.py](scraper.py), [import_external_data.py](import_external_data.py) | Data ingestion. The Selenium scraper runs **locally only** (its packages are commented out in requirements) |

**AI calls:** most features call Groq (`llama-4-scout`, Whisper) first and fall back to Gemini if that fails. Prompts tell the model to return JSON, which is parsed by `extract_json_from_llm`. That helper is copied separately into each router.

**Access rules:**
- Roles are `admin` and `user`.
- Signing up with a `.edu`, `.ac.uk` or `.edu.*` email automatically sets `is_premium=True`.
- Everyone else spends `resume_credits` to run a résumé scan.

**Payments:** there are three tiers: 250, 500 and 750 EGP for 10, 20 and 50 scans. The user pays by InstaPay to a phone number and confirms over WhatsApp. An admin then adds the credits by hand. The tier keys in the code (`"10"`, `"15"`, `"25"`) don't match the scan counts, so take care when editing them.

## Recent work
- Manual InstaPay checkout through WhatsApp; checkout layout fixed for mobile
- Pricing switched to EGP (250 / 500 / 750)
- InstaPay handle changed to a phone number

## Open issues (by priority)
1. **🔴 Free credits for anyone.** `GET /checkout/demo_unlock?tier=25` ([routers/resume.py:66](routers/resume.py#L66)) gives any logged-in user 50 credits, as many times as they want. Remove it or restrict it to admins.
2. **🔴 Default secret key in code.** If `SECRET_KEY` isn't set, the app uses a hard-coded value and sessions can be forged. Make sure production sets it, and have the app refuse to start without it.
3. **🟠 SSL checks off everywhere.** [main.py](main.py) disables HTTPS certificate verification for the whole process.
4. **🟠 Student premium not verified.** Anyone can claim premium by typing an `.edu`-style email; nothing confirms they own the address.
5. **🟡 Retired model.** The Gemini fallbacks use `gemini-1.5-flash`, which no longer exists, so they will fail.
6. **🟡 Repeated code.** The JSON parsing helper and the Groq→Gemini fallback are copied across routers. Move them into one shared module.
7. **🟡 No tests, and the README is out of date.**

## Suggested next steps
1. Fix #1 and #2 before taking more payments.
2. Update the Gemini model names and pull the AI calls into a shared helper.
3. Add basic tests for login and the credit-spending path, then update the README.
