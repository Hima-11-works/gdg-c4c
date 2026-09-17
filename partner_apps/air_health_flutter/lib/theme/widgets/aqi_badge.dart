import 'package:flutter/material.dart';

import '../../domain/models/cpcb_category.dart';
import '../app_colors.dart';
import '../app_spacing.dart';
import '../app_typography.dart';

/// AQI display — the hero element on the Home screen.
///
/// Number + category label. No icon, no colored container around
/// the label, no uppercase letter-spacing. Just two lines of text
/// with the right color.
class AqiBadge extends StatelessWidget {
  const AqiBadge({
    super.key,
    required this.aqi,
    required this.category,
    this.compact = false,
  });

  final int aqi;
  final CpcbCategory category;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final color = AppColors.forCategory(category);
    final cs = Theme.of(context).colorScheme;

    if (compact) {
      return Semantics(
        label: 'Air quality index $aqi. Category: ${category.label}',
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(
              '$aqi',
              style: AppTypography.titleMedium.copyWith(color: color),
            ),
            const SizedBox(width: AppSpacing.sm),
            Text(
              category.label,
              style: AppTypography.labelSmall.copyWith(
                color: cs.onSurfaceVariant,
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
          Text(
            '$aqi',
            style: AppTypography.displayLarge.copyWith(color: color),
            textAlign: TextAlign.center,
          ),
          const SizedBox(height: AppSpacing.sm),
          Text(
            category.label,
            style: AppTypography.titleMedium.copyWith(
              color: cs.onSurfaceVariant,
            ),
            textAlign: TextAlign.center,
          ),
        ],
      ),
    );
  }
}
