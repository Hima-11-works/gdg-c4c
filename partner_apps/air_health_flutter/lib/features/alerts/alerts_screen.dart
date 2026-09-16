import 'package:flutter/material.dart';

import '../../theme/app_spacing.dart';

/// Alerts screen — placeholder. Will show alert history with
/// "Why did I receive this?" explanations.
class AlertsScreen extends StatelessWidget {
  const AlertsScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Alerts')),
      body: const Center(
        child: Padding(
          padding: EdgeInsets.all(AppSpacing.xxl),
          child: Text(
            'Alerts — notification history with explanations goes here.',
            textAlign: TextAlign.center,
          ),
        ),
      ),
    );
  }
}
