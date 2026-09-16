import 'package:flutter/material.dart';

import '../../theme/tokens.dart';

/// Nearby areas screen — placeholder. Will show nearby locations with
/// lower expected pollution, sortable by cleaner-now / cleaner-soon / distance.
class NearbyScreen extends StatelessWidget {
  const NearbyScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Nearby Areas')),
      body: const Center(
        child: Padding(
          padding: EdgeInsets.all(Tokens.sp24),
          child: Text(
            'Nearby — areas with lower expected pollution go here.',
            textAlign: TextAlign.center,
          ),
        ),
      ),
    );
  }
}
