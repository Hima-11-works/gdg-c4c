import 'package:flutter/material.dart';

import '../app_radius.dart';
import '../app_spacing.dart';

/// Standard content card — flat surface with a thin border.
///
/// Colors come from Theme.of(context) so dark mode works automatically.
class AppCard extends StatelessWidget {
  const AppCard({
    super.key,
    required this.child,
    this.padding = AppSpacing.allXl,
    this.margin,
    this.onTap,
  });

  final Widget child;
  final EdgeInsets padding;
  final EdgeInsets? margin;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;

    final card = Container(
      margin: margin,
      padding: padding,
      decoration: BoxDecoration(
        color: cs.surfaceContainerHighest,
        borderRadius: AppRadius.mdAll,
        border: Border.all(color: cs.outlineVariant, width: 0.5),
      ),
      child: child,
    );

    if (onTap != null) {
      return Semantics(
        button: true,
        child: GestureDetector(
          onTap: onTap,
          behavior: HitTestBehavior.opaque,
          child: card,
        ),
      );
    }
    return card;
  }
}
