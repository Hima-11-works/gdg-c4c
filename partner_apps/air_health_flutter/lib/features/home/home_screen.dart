import 'package:flutter/material.dart';

import '../../theme/tokens.dart';

/// Home screen — placeholder. Will show AQI hero, forecast chart,
/// personalized warnings, and data freshness.
class HomeScreen extends StatelessWidget {
  const HomeScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Air Health')),
      body: const Center(
        child: Padding(
          padding: EdgeInsets.all(Tokens.sp24),
          child: Text(
            'Home — AQI hero, forecast chart, and warnings go here.',
            textAlign: TextAlign.center,
          ),
        ),
      ),
    );
  }
}
