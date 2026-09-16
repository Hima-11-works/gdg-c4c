import 'package:flutter/material.dart';

import '../../domain/models/user_alert_preferences.dart';
import '../app_colors.dart';
import '../app_radius.dart';
import '../app_spacing.dart';
import '../app_typography.dart';

/// Alert banner — shown inline on the Home screen when an alert is active.
///
/// Pairs a severity icon with a non-diagnostic message. The border-left
/// accent conveys severity visually; the text carries the same meaning.
class AlertBanner extends StatelessWidget {
  const AlertBanner({
    super.key,
    required this.severity,
    required this.message,
    this.onDismiss,
  });

  final AlertSeverity severity;
  final String message;
  final VoidCallback? onDismiss;

  @override
  Widget build(BuildContext context) {
    final color = _colorForSeverity(severity);
    final icon = _iconForSeverity(severity);
    final label = _labelForSeverity(severity);

    return Semantics(
      label: '$label alert: $message',
      container: true,
      child: Container(
        padding: const EdgeInsets.all(AppSpacing.xl),
        decoration: BoxDecoration(
          color: color.withValues(alpha: 0.08),
          borderRadius: AppRadius.mdAll,
          border: Border(
            left: BorderSide(color: color, width: 3),
          ),
        ),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Icon(icon, size: 20, color: color),
            const SizedBox(width: AppSpacing.md),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    label,
                    style: AppTypography.labelMedium.copyWith(color: color),
                  ),
                  const SizedBox(height: AppSpacing.xs),
                  Text(message, style: AppTypography.bodyMedium),
                ],
              ),
            ),
            if (onDismiss != null)
              IconButton(
                icon: const Icon(Icons.close, size: 18),
                onPressed: onDismiss,
                visualDensity: VisualDensity.compact,
                tooltip: 'Dismiss',
              ),
          ],
        ),
      ),
    );
  }

  static Color _colorForSeverity(AlertSeverity s) {
    return switch (s) {
      AlertSeverity.info => AppColors.info,
      AlertSeverity.advisory => AppColors.aqiModerate,
      AlertSeverity.warning => AppColors.warning,
      AlertSeverity.urgent => AppColors.error,
    };
  }

  static IconData _iconForSeverity(AlertSeverity s) {
    return switch (s) {
      AlertSeverity.info => Icons.info_outline,
      AlertSeverity.advisory => Icons.info_outline,
      AlertSeverity.warning => Icons.warning_amber_outlined,
      AlertSeverity.urgent => Icons.dangerous_outlined,
    };
  }

  static String _labelForSeverity(AlertSeverity s) {
    return switch (s) {
      AlertSeverity.info => 'Info',
      AlertSeverity.advisory => 'Advisory',
      AlertSeverity.warning => 'Warning',
      AlertSeverity.urgent => 'Urgent',
    };
  }
}
