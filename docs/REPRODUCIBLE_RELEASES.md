# Reproducible CI checks and demo builds

`.github/workflows/ci.yml` runs on pull requests, pushes to `beta`, and manual
dispatch. It uses the committed `backend/uv.lock`, `frontend/package-lock.json`,
and Flutter `pubspec.lock` files. Backend checks apply the migration chain and
run unit plus PostgreSQL/PostGIS tests. The dashboard installs with `npm ci`,
then runs lint and production build. Each Flutter project runs `pub get` with
lock enforcement, analysis, tests, and an Android release-mode build.

CI uploads one APK artifact for each app: `air-health-citizen-demo` and
`fire-dept-simulator-demo`. The artifacts are short-lived demo builds, not
store-ready releases: the Android projects currently sign release-mode APKs
with the debug signing config. Production publishing still needs a separately
managed signing key and release process.

The pinned toolchain used by CI is Python 3.12, Node 22.12.0, Flutter 3.44.0,
uv 0.12.5, and Java 17 for the Android builds. Local commands:

```bash
# Backend, from backend/
uv sync --locked --extra dev
uv run --locked ruff check app/services/incident_webhook.py app/services/impact_evidence.py tests/test_incident_webhook_service.py tests/test_impact_evidence.py tests/test_forecasting_service.py
uv run --locked pytest -q

# Dashboard, from frontend/
npm ci
npm run lint
npm run build

# Each Flutter app directory
flutter pub get --enforce-lockfile
flutter analyze
flutter test --no-pub
flutter build apk --release --no-pub
```

The repository's broader legacy Ruff backlog is currently outside the release
gate; CI lints the P1 service and regression-test surfaces it maintains. The
backend unit and PostGIS integration suite still runs in full.
