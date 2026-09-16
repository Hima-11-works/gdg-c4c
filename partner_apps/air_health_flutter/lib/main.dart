import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'app/app.dart';
import 'notifications/notification_service.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();

  // Initialise local notifications (no-op until permissions are granted).
  await NotificationService().initialise();

  runApp(
    const ProviderScope(
      child: AirHealthApp(),
    ),
  );
}
