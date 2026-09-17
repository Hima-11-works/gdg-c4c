import 'package:flutter/material.dart';

import '../domain/models/cpcb_category.dart';
import 'app_colors.dart';

/// Re-export of CPCB category colors.
///
/// Delegates to [AppColors] so there is one source of truth.
/// Kept for backward compatibility — prefer importing AppColors directly.
abstract final class CpcbColors {
  static Color good = AppColors.aqiGood;
  static Color satisfactory = AppColors.aqiSatisfactory;
  static Color moderate = AppColors.aqiModerate;
  static Color poor = AppColors.aqiPoor;
  static Color veryPoor = AppColors.aqiVeryPoor;
  static Color severe = AppColors.aqiSevere;

  static Color forCategory(CpcbCategory category) =>
      AppColors.forCategory(category);
}
