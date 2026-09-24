/// Where the console points, and who it acts as.
///
/// All three come from `--dart-define` at build time and are editable at runtime
/// in Settings, held in memory. Deliberately not persisted to disk: persisting
/// would need a storage package, and this app is built to depend on nothing but
/// the Flutter SDK. The cost is that a restart forgets an edited value, which is
/// why the build-time values exist.
///
///   flutter run --dart-define=INCIDENT_API_BASE_URL=http://10.0.2.2:8001 \
///               --dart-define=SIMULATOR_API_KEY=sim-local-dev-key \
///               --dart-define=SIMULATOR_ACTOR_ID=engine-7
///
/// (`10.0.2.2` is how an Android emulator reaches the host's localhost.)
///
/// The key and the actor are different things and both are required: the key
/// authenticates the *deployment*, the actor id names *which responder within
/// it* is acting. The service resolves that actor's role and jurisdiction from
/// its own registry, so this console cannot widen its own authority by
/// configuration — an unregistered id is refused, and so is an actor whose role
/// does not match the incident.
library;

import 'models.dart';

class SimulatorConfig {
  SimulatorConfig({
    required this.baseUrl,
    required this.apiKey,
    required this.actorId,
    required this.role,
  });

  final String baseUrl;

  /// The simulator key sent as `X-Simulator-Key` on every write. Empty means
  /// no key is configured, and this console can read but not change anything.
  final String apiKey;

  /// The responder this console acts as, sent as `X-Actor-Id` on every write.
  /// Empty means no actor is configured, and every write is refused.
  final String actorId;

  /// The role this console *serves* — used to filter the queue it shows, not to
  /// authorise anything. Authority comes from the actor's registry entry.
  final ResponderRole role;

  bool get hasKey => apiKey.trim().isNotEmpty;
  bool get hasActor => actorId.trim().isNotEmpty;
  bool get canWrite => hasKey && hasActor;

  /// Why writes are unavailable, when they are.
  String? get writeBlocker {
    if (!hasKey) {
      return 'No simulator key is configured, so this console cannot change anything. '
          'Set it in Settings — writes are refused without it.';
    }
    if (!hasActor) {
      return 'No actor is configured, so this console cannot change anything. Every write '
          'must name a responder with X-Actor-Id; set one in Settings.';
    }
    return null;
  }

  SimulatorConfig copyWith({String? baseUrl, String? apiKey, String? actorId}) =>
      SimulatorConfig(
        baseUrl: baseUrl ?? this.baseUrl,
        apiKey: apiKey ?? this.apiKey,
        actorId: actorId ?? this.actorId,
        role: role,
      );

  static const defaultBaseUrl = String.fromEnvironment(
    'INCIDENT_API_BASE_URL',
    defaultValue: 'http://localhost:8001',
  );

  static const defaultApiKey = String.fromEnvironment(
    'SIMULATOR_API_KEY',
    defaultValue: '',
  );

  static const defaultActorId = String.fromEnvironment(
    'SIMULATOR_ACTOR_ID',
    defaultValue: '',
  );

  factory SimulatorConfig.fromEnvironment() => SimulatorConfig(
        baseUrl: defaultBaseUrl,
        apiKey: defaultApiKey,
        actorId: defaultActorId,
        role: ResponderRole.fireDepartment,
      );
}
