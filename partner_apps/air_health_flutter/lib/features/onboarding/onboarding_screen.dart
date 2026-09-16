import 'package:flutter/material.dart';

import '../../theme/app_spacing.dart';

/// Onboarding flow — placeholder. Will collect health context and
/// alert sensitivity with explicit user confirmation.
class OnboardingScreen extends StatelessWidget {
  const OnboardingScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Welcome')),
      body: const Center(
        child: Padding(
          padding: EdgeInsets.all(AppSpacing.xxl),
          child: Text(
            'Onboarding — health context and sensitivity selection go here.',
            textAlign: TextAlign.center,
          ),
        ),
      ),
    );
  }
}
