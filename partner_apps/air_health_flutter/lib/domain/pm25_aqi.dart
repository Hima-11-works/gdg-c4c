import 'models/models.dart';

/// Converts a PM2.5 concentration (µg/m³) to an approximate CPCB AQI.
///
/// The grid API reports PM2.5 only, so the app derives an AQI from the CPCB
/// PM2.5 sub-index (piecewise-linear between the official band boundaries).
/// The published CPCB AQI is the *maximum* sub-index across pollutants
/// (PM10, NO2, SO2, ...); with only PM2.5 available this is the PM2.5
/// sub-index alone, which is the dominant pollutant on most days in the
/// regions this platform covers. Treat it as an approximation, not a
/// certified AQI.
///
/// Result is clamped to the CPCB scale [0, 500].
int pm25ToCpcbAqi(double pm25) {
  if (pm25.isNaN) return 0;
  final concentration = pm25 < 0 ? 0.0 : pm25;

  for (final band in _bands) {
    if (concentration <= band.concHigh) {
      final span = band.concHigh - band.concLow;
      final t = span == 0 ? 0.0 : (concentration - band.concLow) / span;
      final aqi = band.aqiLow + t * (band.aqiHigh - band.aqiLow);
      return aqi.round().clamp(0, 500).toInt();
    }
  }
  return 500;
}

/// CPCB category for a PM2.5 concentration.
CpcbCategory pm25ToCategory(double pm25) =>
    CpcbCategory.fromAqi(pm25ToCpcbAqi(pm25));

/// CPCB PM2.5 (24-hour) band boundaries: concentration µ/m³ → AQI sub-index.
class _Pm25Band {
  const _Pm25Band(this.concLow, this.concHigh, this.aqiLow, this.aqiHigh);

  final double concLow;
  final double concHigh;
  final int aqiLow;
  final int aqiHigh;
}

const List<_Pm25Band> _bands = <_Pm25Band>[
  _Pm25Band(0, 30, 0, 50), // Good
  _Pm25Band(30, 60, 50, 100), // Satisfactory
  _Pm25Band(60, 90, 100, 200), // Moderately Polluted
  _Pm25Band(90, 120, 200, 300), // Poor
  _Pm25Band(120, 250, 300, 400), // Very Poor
  _Pm25Band(250, 500, 400, 500), // Severe
];
