/// Design tokens — the single source of truth for spacing, radius,
/// and elevation. Every widget imports this instead of hardcoding values.
abstract final class Tokens {
  // Spacing (8pt grid)
  static const double sp2 = 2;
  static const double sp4 = 4;
  static const double sp8 = 8;
  static const double sp12 = 12;
  static const double sp16 = 16;
  static const double sp24 = 24;
  static const double sp32 = 32;
  static const double sp48 = 48;
  static const double sp64 = 64;

  // Corner radius
  static const double radiusSm = 8;
  static const double radiusMd = 12;
  static const double radiusLg = 16;

  // Elevation
  static const double elevNone = 0;
  static const double elevSm = 1;
  static const double elevMd = 2;

  // AQI hero sizing
  static const double aqiNumberFontSize = 64;
  static const double aqiCategoryFontSize = 16;
}
