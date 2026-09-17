import 'package:flutter/material.dart';

import '../app_spacing.dart';
import '../app_typography.dart';

/// Inline forecast summary: "Expected to reach Poor around 6:30 PM".
///
/// Plain text with a subtle muted style. No container, no icon,
/// no decoration — just the information.
class ForecastStatus extends StatelessWidget {
  const ForecastStatus({
    super.key,
    required this.message,
  });

  final String message;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;

    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: AppSpacing.xl),
      child: Text(
        message,
        style: AppTypography.bodyMedium.copyWith(
          color: cs.onSurfaceVariant,
        ),
      ),
    );
  }
}
