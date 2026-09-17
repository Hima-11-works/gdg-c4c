import 'package:flutter/material.dart';

import '../app_colors.dart';
import '../app_radius.dart';
import '../app_spacing.dart';
import '../app_typography.dart';

/// A small chip that pairs a color with a text label.
///
/// Background tint only — no border. Text is always present so
/// color is never the sole carrier of meaning.
class StatusChip extends StatelessWidget {
  const StatusChip({
    super.key,
    required this.label,
    required this.color,
    this.icon,
  });

  final String label;
  final Color color;
  final IconData? icon;

  @override
  Widget build(BuildContext context) {
    return Semantics(
      label: label,
      child: Container(
        padding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.md,
          vertical: AppSpacing.xs,
        ),
        decoration: BoxDecoration(
          color: color.withValues(alpha: 0.10),
          borderRadius: AppRadius.smAll,
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            if (icon != null) ...[
              Icon(icon, size: 14, color: color),
              const SizedBox(width: AppSpacing.xs),
            ],
            Text(
              label,
              style: AppTypography.labelSmall.copyWith(color: color),
            ),
          ],
        ),
      ),
    );
  }
}

/// Pre-built trend chips.
class TrendChip extends StatelessWidget {
  const TrendChip.improving({super.key})
      : label = 'Improving',
        color = AppColors.aqiGood,
        icon = Icons.trending_down;

  const TrendChip.stable({super.key})
      : label = 'Stable',
        color = AppColors.onSurfaceMuted,
        icon = Icons.trending_flat;

  const TrendChip.worsening({super.key})
      : label = 'Worsening',
        color = AppColors.warning,
        icon = Icons.trending_up;

  final String label;
  final Color color;
  final IconData icon;

  @override
  Widget build(BuildContext context) {
    return StatusChip(label: label, color: color, icon: icon);
  }
}
