import 'package:flutter/material.dart';

import '../../theme/tokens.dart';

/// Profile / settings screen — placeholder. Will show health context,
/// sensitivity tier editor, data management, and privacy controls.
class ProfileScreen extends StatelessWidget {
  const ProfileScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Profile & Settings')),
      body: const Center(
        child: Padding(
          padding: EdgeInsets.all(Tokens.sp24),
          child: Text(
            'Profile — health context, sensitivity, and data management go here.',
            textAlign: TextAlign.center,
          ),
        ),
      ),
    );
  }
}
