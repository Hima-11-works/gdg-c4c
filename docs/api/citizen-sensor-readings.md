# Citizen sensor readings

These endpoints accept PM2.5 values copied from an external consumer air-quality
meter. A phone does not measure PM2.5. Readings are stored in
`citizen_sensor_submission`, a separate table from provider-backed
`sensor_reading`; neither pending nor reviewed community submissions feed the
current grid or forecast pipeline.

## Submit a reading

`POST /api/v1/sensors/citizen` accepts:

```json
{
  "client_submission_id": "flutter-sensor-unique-id",
  "latitude": 28.6139,
  "longitude": 77.2090,
  "pm25_ugm3": 42.5,
  "device_label": "PurpleAir PA-II",
  "measured_at": "2026-09-28T10:30:00Z",
  "consent": true
}
```

The platform requires consent, India coordinates, a timezone-aware timestamp
no older than 24 hours, and PM2.5 in µg/m³ from 0 through 2000. The endpoint is
idempotent by `client_submission_id`, and uses the citizen report per-network
and platform hourly limits. New values start with `status: pending_review`.

## Authority review

`GET /api/v1/sensors/citizen` returns pending submissions. Both this endpoint
and `POST /api/v1/sensors/citizen/{id}/review` require the configured
`X-Reviewer-Key` used by the citizen-report review endpoints. Review bodies are
`{"status":"verified"}` or `{"status":"rejected"}`. The review status is an
audit label only; it does not make a reading a calibrated station or add it to
forecasts.

The API returns citizen readings as distinct, unverified submissions. It does
not disclose the stored coarse network prefix.
