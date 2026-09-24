/// Fire Department Simulator — a response console for the Air Health incident
/// workflow.
///
/// This is a **simulation**. It reads and writes the backend's incident records
/// (the same rows the web console and any other client see), which makes it a
/// genuine operational tool for practising the workflow — and not a real
/// dispatch system. Nothing here notifies anyone.
///
/// Built on nothing but the Flutter SDK: no pub packages. See README.md for why,
/// and for the run command that points it at a backend.
library;

import 'package:flutter/material.dart';

import 'src/api.dart';
import 'src/config.dart';
import 'src/screens/incidents_screen.dart';
import 'src/screens/settings_screen.dart';

void main() {
  runApp(const FireDeptSimulatorApp());
}

class FireDeptSimulatorApp extends StatefulWidget {
  const FireDeptSimulatorApp({super.key});

  @override
  State<FireDeptSimulatorApp> createState() => _FireDeptSimulatorAppState();
}

class _FireDeptSimulatorAppState extends State<FireDeptSimulatorApp> {
  SimulatorConfig _config = SimulatorConfig.fromEnvironment();

  /// One client for the app's lifetime, rebuilt when the settings change so a
  /// new address, key or actor takes effect immediately.
  late IncidentApi _api = IncidentApi(
    baseUrl: _config.baseUrl,
    apiKey: _config.apiKey,
    actorId: _config.actorId,
  );

  @override
  void dispose() {
    _api.close();
    super.dispose();
  }

  void _saveConfig(SimulatorConfig updated) {
    setState(() {
      _api.close();
      _config = updated;
      _api = IncidentApi(
        baseUrl: updated.baseUrl,
        apiKey: updated.apiKey,
        actorId: updated.actorId,
      );
    });
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Fire Dept Simulator',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        // Calm blue rather than alarm red: a simulation should not look like an
        // emergency, and red is kept for severity and failures.
        colorSchemeSeed: Colors.indigo,
        useMaterial3: true,
      ),
      home: Builder(
        builder: (context) => IncidentsScreen(
          api: _api,
          config: _config,
          onOpenSettings: () => Navigator.of(context).push(
            MaterialPageRoute<void>(
              builder: (_) => SettingsScreen(config: _config, onSave: _saveConfig),
            ),
          ),
        ),
      ),
    );
  }
}
