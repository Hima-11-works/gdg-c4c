/// Where the console points, and who it acts as.
///
/// Both come from `--dart-define` at build time and are editable at runtime in
/// Settings, held in memory. Deliberately not persisted to disk: persisting
/// would need a storage package, and this app is built to depend on nothing but
/// the Flutter SDK. The cost is that a restart forgets an edited key, which is
/// why the build-time values exist.
///
///   flutter run --dart-define=INCIDENT_API_BASE_URL=http://10.0.2.2:8000 \
///               --dart-define=SIMULATOR_API_KEY=sim-local-dev-key
///
/// (`10.0.2.2` is how an Android emulator reaches the host's localhost.)
library;

import 'models.dart';

class SimulatorConfig {
  SimulatorConfig({required this.baseUrl, required this.apiKey, required this.role, required this.actorId});

  final String baseUrl;

  /// The simulator key sent as `X-Simulator-Key` on every write. Empty means
  /// no key is configured, and this console can read but not change anything.
  final String apiKey;

  /// The role this console acts as. Fixed to the fire department: this is the
  /// fire-department simulator, and the API refuses a role that does not match
  /// an incident's `responder_role`.
  final ResponderRole role;
  final String actorId;

  bool get hasKey => apiKey.trim().isNotEmpty;

  SimulatorConfig copyWith({String? baseUrl, String? apiKey, ResponderRole? role, String? actorId}) => SimulatorConfig(
        baseUrl: baseUrl ?? this.baseUrl,
        apiKey: apiKey ?? this.apiKey,
        role: role ?? this.role,
        actorId: actorId ?? this.actorId,
      );

  static const defaultBaseUrl = String.fromEnvironment(
    'INCIDENT_API_BASE_URL',
    defaultValue: 'https://air-health-api.vercel.app',
  );

  static const defaultApiKey = String.fromEnvironment(
    'SIMULATOR_API_KEY',
    defaultValue: '',
  );

  static const defaultActorId = String.fromEnvironment(
    'SIMULATOR_ACTOR_ID',
    defaultValue: 'fire-unit-7',
  );

  static const defaultRoleName = String.fromEnvironment(
    'SIMULATOR_ROLE',
    defaultValue: 'fire_department',
  );

  factory SimulatorConfig.fromEnvironment() => SimulatorConfig(
        baseUrl: defaultBaseUrl,
        apiKey: defaultApiKey,
        role: defaultRoleName == 'pollution_control'
            ? ResponderRole.pollutionControl
            : ResponderRole.fireDepartment,
        actorId: defaultActorId,
      );
}
