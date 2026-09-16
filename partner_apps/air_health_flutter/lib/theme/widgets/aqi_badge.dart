import 'package:flutter/material.dart';

import '../../domain/models/cpcb_category.dart';
import '../app_colors.dart';
import '../app_radius.dart';
import '../app_spacing.dart';
import '../app_typography.dart';

/// Prominent AQI display — the hero element on the Home screen.
///
/// Shows the numeric AQI value, CPCB category label, and an icon.
/// Color is never the sole carrier of meaning — the category label
/// is always rendered alongside.
class AqiBadge extends StatelessWidget {
  const AqiBadge({
    super.key,
    required this.aqi,
    required this.category,
    this.compact = false,
  });

  final int aqi;
  final CpcbCategory category;

  /// If true, renders a smaller inline version suitable for list tiles.
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final color = AppColors.forCategory(category);
    final icon = _iconForCategory(category);

    if (compact) {
      return Semantics(
        label: 'Air quality index $aqi. Category: ${category.label}',
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 16, color: color),
            const SizedBox(width: AppSpacing.sm),
            Text(
              '$aqi',
              style: AppTypography.titleMedium.copyWith(color: color),
            ),
            const SizedBox(width: AppSpacing.sm),
            Text(
              category.label,
              style: AppTypography.labelSmall.copyWith(
                color: AppColors.onSurfaceMuted,
              ),
            ),
          ],
        ),
      );
    }

    return Semantics(
      label: 'Air quality index $aqi. Category: ${category.label}',
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 28, color: color),
          const SizedBox(height: AppSpacing.sm),
          Text(
            '$aqi',
            style: AppTypography.displayLarge.copyWith(color: color),
            textAlign: TextAlign.center,
          ),
          const SizedBox(height: AppSpacing.sm),
          Container(
            padding: const EdgeInsets.symmetric(
              horizontal: AppSpacing.lg,
              vertical: AppSpacing.sm,
            ),
            decoration: BoxDecoration(
              color: color.withValues(alpha: 0.12),
              borderRadius: AppRadius.smAll,
            ),
            child: Text(
              category.label.toUpperCase(),
              style: AppTypography.labelMedium.copyWith(
                color: color,
                fontWeight: FontWeight.w700,
                letterSpacing: 1,
              ),
              textAlign: TextAlign.center,
            ),
          ),
        ],
      ),
    );
  }

  IconData _iconForCategory(CpcbCategory cat) {
    return switch (cat) {
      CpcbCategory.good => Icons.check_circle_outline,
      CpcbCategory.satisfactory => Icons.check_circle_outline,
      CpcbCategory.moderate => Icons.info_outline,
      CpcbCategory.poor => Icons.warning_amber_outlined,
      CpcbCategory.veryPoor => Icons.warning_amber_outlined,
      CpcbCategory.severe => Icons.dangerous_outlined,
    };
  }
}
