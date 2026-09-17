import 'package:flutter/material.dart';

import '../domain/models/cpcb_category.dart';

/// App-wide color palette.
///
/// Neutral surfaces come from Material 3's [ColorScheme].
/// CPCB severity colors appear ONLY through [forCategory] — never on
/// generic cards, buttons, or decorative surfaces.
abstract final class AppColors {
  // ── Neutral palette ──────────────────────────────────────────────────
  static const Color surface = Color(0xFFFAFAFA);
  static const Color surfaceDim = Color(0xFFF0F0F0);
  static const Color surfaceContainer = Color(0xFFEEEEEE);
  static const Color onSurface = Color(0xFF1C1C1C);
  static const Color onSurfaceMuted = Color(0xFF6B6B6B);
  static const Color outline = Color(0xFFD0D0D0);
  static const Color outlineVariant = Color(0xFFE0E0E0);
  static const Color divider = Color(0xFFE5E5E5);

  // Dark-mode equivalents (used by AppTheme.dark).
  static const Color surfaceDark = Color(0xFF121212);
  static const Color surfaceDimDark = Color(0xFF1E1E1E);
  static const Color surfaceContainerDark = Color(0xFF2A2A2A);
  static const Color onSurfaceDark = Color(0xFFE8E8E8);
  static const Color onSurfaceMutedDark = Color(0xFF9E9E9E);
  static const Color outlineDark = Color(0xFF444444);
  static const Color outlineVariantDark = Color(0xFF333333);
  static const Color dividerDark = Color(0xFF2E2E2E);

  // ── Semantic status colors (NOT CPCB) ────────────────────────────────
  static const Color success = Color(0xFF2E7D32);
  static const Color warning = Color(0xFFEF6C00);
  static const Color error = Color(0xFFC62828);
  static const Color info = Color(0xFF1565C0);

  // ── CPCB AQI category colors ─────────────────────────────────────────
  static const Color aqiGood = Color(0xFF4CAF50);
  static const Color aqiSatisfactory = Color(0xFF8BC34A);
  static const Color aqiModerate = Color(0xFFFFC107);
  static const Color aqiPoor = Color(0xFFFF9800);
  static const Color aqiVeryPoor = Color(0xFFF44336);
  static const Color aqiSevere = Color(0xFF7B1FA2);

  /// Map a [CpcbCategory] to its canonical color.
  static Color forCategory(CpcbCategory category) {
    return switch (category) {
      CpcbCategory.good => aqiGood,
      CpcbCategory.satisfactory => aqiSatisfactory,
      CpcbCategory.moderate => aqiModerate,
      CpcbCategory.poor => aqiPoor,
      CpcbCategory.veryPoor => aqiVeryPoor,
      CpcbCategory.severe => aqiSevere,
    };
  }

  /// Alert severity colors.
  static Color forAlertSeverity(String severity) {
    return switch (severity) {
      'info' => info,
      'advisory' => aqiModerate,
      'warning' => warning,
      'urgent' => error,
      _ => onSurfaceMuted,
    };
  }
}
