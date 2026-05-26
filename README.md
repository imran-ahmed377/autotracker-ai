# autotracker-ai

Daily weather and environment tracker powered by the free Open-Meteo API.

## What this project does

- Pulls weather data from Open-Meteo (no API key required).
- Tracks multiple global cities with:
  - recent history (`past_days`)
  - near-term forecast (`forecast_days`)
  - current conditions
- Generates climate trend charts per city.
- Builds a static dashboard page and publishes it to GitHub Pages.
- Commits generated CSV data back to the repository on scheduled runs.

## Data source

- API: `https://api.open-meteo.com/v1/forecast`
- Free access, no key required.

## Local run

```bash
python -m venv .venv
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python weather_dashboard.py
```

## Environment variables

Optional tuning values:

- `PAST_DAYS` (default: `30`)
- `FORECAST_DAYS` (default: `7`)

Example:

```bash
PAST_DAYS=60 FORECAST_DAYS=10 python weather_dashboard.py
```

## Generated files

- Data:
  - `output/weather_daily.csv`
  - `output/weather_observations.csv`
- Dashboard assets:
  - `docs/index.html`
  - `docs/charts/*.png`
  - `docs/.nojekyll`

## GitHub Actions and Pages

Workflow file: `.github/workflows/weather-dashboard.yml`

Schedule:

- `0 2 * * *` (daily at 02:00 UTC)

The workflow:

1. Runs `python weather_dashboard.py`
2. Commits updated data/dashboard files
3. Uploads `docs/` as a Pages artifact
4. Deploys the dashboard to GitHub Pages

You can also trigger runs manually from the Actions tab using `workflow_dispatch`.
