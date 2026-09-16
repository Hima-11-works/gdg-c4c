import 'package:flutter/material.dart';

import '../app_colors.dart';
import '../app_radius.dart';
import '../app_spacing.dart';
import '../app_typography.dart';

/// Inline forecast summary: "Expected to reach Poor around 6:30 PM".
///
/// Pairs an icon with a calm, non-diagnostic text line.
class ForecastStatus extends StatelessWidget {
  const ForecastStatus({
    super.key,
    required this.message,
    this.confidence,
  });

  final String message;

  /// Optional confidence string (e.g. "85% confidence").
  final String? confidence;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.xl,
        vertical: AppSpacing.lg,
      ),
      decoration: BoxDecoration(
        color: AppColors.surfaceDim,
        borderRadius: AppRadius.smAll,
      ),
      child: Row(
        children: [
          const Icon(
            Icons.schedule_outlined,
            size: 18,
            color: AppColors.onSurfaceMuted,
          ),
          const SizedBox(width: AppSpacing.md),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(message, style: AppTypography.bodyMedium),
                if (confidence != null) ...[
                  const SizedBox(height: AppSpacing.xs),
                  Text(
                    confidence!,
                    style: AppTypography.labelSmall.copyWith(
                      color: AppColors.onSurfaceMuted,
                    ),
                  ),
                ],
              ],
            ),
          ),
        ],
      ),
    );
  }
}
