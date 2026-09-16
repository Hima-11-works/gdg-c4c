import 'package:flutter/material.dart';

import '../domain/models/cpcb_category.dart';

/// CPCB category colors — used ONLY where they label severity
/// (AQI hero, chart bands, category chips). Never on generic cards,
/// buttons, or decorative surfaces.
abstract final class CpcbColors {
  static const Color good = Color(0xFF4CAF50);
  static const Color satisfactory = Color(0xFF8BC34A);
  static const Color moderate = Color(0xFFFFC107);
  static const Color poor = Color(0xFFFF9800);
  static const Color veryPoor = Color(0xFFF44336);
  static const Color severe = Color(0xFF7B1FA2);

  static Color forCategory(CpcbCategory category) {
    return switch (category) {
      CpcbCategory.good => good,
      CpcbCategory.satisfactory => satisfactory,
      CpcbCategory.moderate => moderate,
      CpcbCategory.poor => poor,
      CpcbCategory.veryPoor => veryPoor,
      CpcbCategory.severe => severe,
    };
  }
}
