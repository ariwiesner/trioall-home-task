# Squanchy Bakery - Fridge Temperature Monitor
Summer, operations manager at Squanchy Bakery, currently combines temperature-logger files from multiple branches into spreadsheets every week. This project replaces that workflow with a single place to upload logger files, identify refrigerators that need attention, and review historical temperature issues.

See Notes.md for the product decisions, assumptions, open questions, unfinished work, and Ai usage.

## Tech Stack
* **Backend:** Django + Django REST Framework
* **Frontend:** Angular
* **Database:** PostgreSQL
* **Containerization:** Docker Compose
i chose technologies i already know well, so the limited implementation time could focus on data normalization, analysis, and product decisions and not on learning a new stack.

## Flow
**Upload → Parse → Validate → Normalize → Store → Analyze → Dashboard**

## Running the Project
### Prerequisite
git clone https://github.com/ariwiesner trioall-home-task.git

cd trioall-home-task

docker compose up --build


## Trying the Demo
The registry is seeded, but temperature readings are intentionally empty. Upload a CSV or Excel file from the **Upload** page.

The importer supports files containing:

* `Time` or `Date` + `Time`
* `Temperature`
* `Logger` (preferred; a fallback logger can also be selected during upload)

Example:
```csv
Logger,Time,Temperature
TL-0512,2026-10-05 10:00,4.0
TL-0512,2026-10-05 10:15,4.2
TL-0388,2026-10-05 10:00,4.6
TL-0388,2026-10-05 10:15,5.4
TL-0388,2026-10-05 10:30,6.3
TL-0388,2026-10-05 10:45,7.1
```

The sample demonstrates a normal refrigerator and a sustained temperature breach.

For a fuller test of the messy input cases from Summer's email, see the sample data / `Notes.md`.

## Tests
Backend:
```bash
docker compose exec backend python manage.py test
```

Frontend tests require Chrome/Chromium and can be run from the host:
```bash
cd frontend
npm install
npx ng test --watch=false --browsers=ChromeHeadless
```

## Notes
See Notes.md for:
* product and data-model decisions
* assumptions made where the requirements were ambiguous
* questions to clarify before production
* unfinished work / what i would improve with more time
* Ai-assisted development and an example of an Ai mistake that was caught and corrected
