/// Single source for the user-facing app name and version.
///
/// The name must match the Android label; [version] must be kept in sync
/// with `version:` in pubspec.yaml (the `+build` number is intentionally not
/// shown). Reading `pubspec.yaml` at runtime would need `package_info_plus`;
/// this constant keeps the Settings screen honest without a new dependency.
abstract final class AppInfo {
  static const String name = 'Air Health';

  /// Keep in sync with pubspec.yaml `version:` (e.g. "1.0.0+1" -> "1.0.0").
  static const String version = '1.0.0';

  static const String tagline = 'Personal air-quality awareness';
}
