# Signal Desk

A single-workspace competitor research tool for iPaaS sales and product teams.

[Deploy to Render](https://render.com/deploy?repo=https://github.com/SumanBadakada/Signal-Desk) and grant Render access to this repository. Once the service is live, open its URL and sign in as `analyst` with the generated `SIGNAL_PASSWORD` from the service’s Environment settings. The free instance sleeps when idle, and its local research history can reset on restart or redeploy.

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --port 8000
```

Open `http://localhost:8000`. The first launch researches an agency reporting scenario. Use **New research** to choose requirements, **Compare** to see coverage, and **Inspect** to review citations. Describe a buyer workflow and choose **Suggest requirements** to prefill recognized terms. Review the selections before running.

Discovery reads the Cloud Services section of Awesome ETL. Evidence checks use selected vendor-maintained documents on GitHub. Unknown means the collected sources do not establish support. Coverage counts individual capabilities and does not certify a complete pipeline.

The versioned API is documented at `/docs`. SQLite stores runs, source snapshots, and reviews in `.signal-data/` (override with `SIGNAL_DATA`). A background worker resumes interrupted runs. Use Python 3.11+ on Linux or macOS. Use locally or behind authenticated internal access before sharing.
