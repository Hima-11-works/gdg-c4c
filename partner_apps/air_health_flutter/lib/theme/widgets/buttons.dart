import 'package:flutter/material.dart';

import '../app_colors.dart';
import '../app_typography.dart';

/// Primary filled button — the main call-to-action.
class PrimaryButton extends StatelessWidget {
  const PrimaryButton({
    super.key,
    required this.label,
    required this.onPressed,
    this.icon,
    this.enabled = true,
  });

  final String label;
  final VoidCallback? onPressed;
  final IconData? icon;
  final bool enabled;

  @override
  Widget build(BuildContext context) {
    final effectiveOnPressed = enabled ? onPressed : null;

    if (icon != null) {
      return ElevatedButton.icon(
        onPressed: effectiveOnPressed,
        icon: Icon(icon, size: 18),
        label: Text(label),
      );
    }
    return ElevatedButton(
      onPressed: effectiveOnPressed,
      child: Text(label),
    );
  }
}

/// Secondary outlined button — lower-emphasis actions.
class SecondaryButton extends StatelessWidget {
  const SecondaryButton({
    super.key,
    required this.label,
    required this.onPressed,
    this.icon,
  });

  final String label;
  final VoidCallback? onPressed;
  final IconData? icon;

  @override
  Widget build(BuildContext context) {
    if (icon != null) {
      return OutlinedButton.icon(
        onPressed: onPressed,
        icon: Icon(icon, size: 18),
        label: Text(label),
      );
    }
    return OutlinedButton(
      onPressed: onPressed,
      child: Text(label),
    );
  }
}

/// Text-only button — the least prominent option.
class SubtleButton extends StatelessWidget {
  const SubtleButton({
    super.key,
    required this.label,
    required this.onPressed,
  });

  final String label;
  final VoidCallback? onPressed;

  @override
  Widget build(BuildContext context) {
    return TextButton(
      onPressed: onPressed,
      child: Text(
        label,
        style: AppTypography.labelLarge.copyWith(
          color: AppColors.onSurfaceMuted,
        ),
      ),
    );
  }
}
