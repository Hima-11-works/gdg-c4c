/// India CPCB AQI categories.
///
/// Values are the upper bound of each band (inclusive).
/// Colors live in theme/cpcb_colors.dart — this enum is pure domain.
enum CpcbCategory {
  good('Good', 50),
  satisfactory('Satisfactory', 100),
  moderate('Moderately Polluted', 200),
  poor('Poor', 300),
  veryPoor('Very Poor', 400),
  severe('Severe', 500);

  const CpcbCategory(this.label, this.upperBound);

  /// Human-readable label shown in the UI.
  final String label;

  /// Inclusive upper AQI value for this band.
  final int upperBound;

  /// Classify an integer AQI value.
  static CpcbCategory fromAqi(int aqi) {
    for (final cat in values) {
      if (aqi <= cat.upperBound) return cat;
    }
    return severe;
  }
}
