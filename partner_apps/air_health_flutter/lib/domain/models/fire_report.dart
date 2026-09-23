/// Citizen fire/burning reports and the contract for submitting them.
///
/// These mirror the backend's POST /api/v1/reports contract (see
/// app.services.reports on the server): the smoke slider is a *triage
/// choice* the backend scales a modeled plume from, never a measurement.
enum FireKind {
  buildingFire('building_fire', 'Building fire'),
  industrialFire('industrial_fire', 'Industrial fire'),
  forestFire('forest_fire', 'Forest fire'),
  cropBurning('crop_burning', 'Wood / crop burning'),
  other('other', 'Other burning');

  const FireKind(this.value, this.label);

  /// Wire value sent to the backend.
  final String value;

  /// Human-readable label for the report form.
  final String label;

  static FireKind fromValue(String value) => FireKind.values.firstWhere(
        (k) => k.value == value,
        orElse: () => FireKind.other,
      );
}

/// The verification state of every resident submission.
///
/// Not a status the backend reports: the report response carries no
/// verification field, and the published README states plainly that a citizen
/// report is "subjective, often non-numeric, and unverified", with any trust or
/// moderation layer explicitly unbuilt. So this is not a guess about a
/// particular row — it is the only true description of every row the endpoint
/// can hold, and the UI shows it wherever a resident's number or note appears
/// so it is never read as a measurement.
///
/// If the backend ever grows a real verification field, this is the one place
/// to replace with that field's value.
class CitizenReportVerification {
  const CitizenReportVerification._();

  static const label = 'Unverified';
  static const detail = 'resident submitted, not a measurement';
  static const badge = '$label — $detail';
  static const tooltip =
      'Submitted by a resident, not a sensor or a satellite. Nothing checks it '
      'before it is stored, and the backend reports no verification status for '
      'these reports, so treat it as a concern raised rather than a measurement.';

  /// What the report sheet says about the local reading: it is not
  /// transmitted, because no endpoint accepts one.
  static const localOnlyDetail =
      'Kept on this device. There is no sensor upload endpoint, so this is not '
      'sent anywhere.';
}

/// A reading the resident typed in from a monitor they own.
///
/// Local only: POST /api/v1/reports takes no sensor value and there is no
/// sensor write route, so this never leaves the device. [tryCreate] returns
/// null for anything that is not a non-negative finite number, which is what
/// the form uses to decide whether the value can be shown at all.
class LocalSensorReading {
  const LocalSensorReading({required this.value, required this.unit});

  final double value;
  final String unit;

  /// Units offered by the form. Free-text "other" is deliberately not a
  /// separate unit — the value is a note to the resident, not a measurement.
  static const units = <String>['µg/m³', 'ppm', 'AQI', 'other'];

  static LocalSensorReading? tryCreate({
    required String rawValue,
    required String unit,
  }) {
    final parsed = double.tryParse(rawValue.trim());
    if (parsed == null || !parsed.isFinite || parsed < 0) return null;
    return LocalSensorReading(value: parsed, unit: unit);
  }

  /// "145 µg/m³" — whole numbers stay whole.
  String get display {
    final rounded = value == value.roundToDouble();
    return '${rounded ? value.toStringAsFixed(0) : value.toStringAsFixed(1)} $unit';
  }

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is LocalSensorReading && value == other.value && unit == other.unit;

  @override
  int get hashCode => Object.hash(value, unit);

  @override
  String toString() => 'LocalSensorReading($display)';
}

/// How long the user estimates the burning has been going, as form options.
///
/// A slider is wrong for this: the answer is fuzzy ("a couple of hours"),
/// so the form offers buckets and this class owns the mapping to the
/// fractional hours the backend stores.
class FireDurationOption {
  const FireDurationOption(this.label, this.hours);

  final String label;
  final double hours;

  static const justStarted = FireDurationOption('Just started', 0.0);
  static const underAnHour = FireDurationOption('Under an hour', 0.5);
  static const oneToThreeHours = FireDurationOption('1-3 hours', 2.0);
  static const threeToSixHours = FireDurationOption('3-6 hours', 4.5);
  static const moreThanSixHours = FireDurationOption('More than 6 hours', 12.0);

  static const all = <FireDurationOption>[
    justStarted,
    underAnHour,
    oneToThreeHours,
    threeToSixHours,
    moreThanSixHours,
  ];
}

/// Everything the user picked, before submission. [create] validates so the
/// UI can only ever hand the transport layer a complete, in-range draft —
/// the same bounds the backend enforces — and throws [ArgumentError]
/// otherwise, which the UI surfaces as a message instead of a 422.
class FireReportDraft {
  const FireReportDraft({
    required this.latitude,
    required this.longitude,
    required this.kind,
    required this.smokeIntensity,
    required this.durationHours,
    this.notes,
    this.clientReportId,
  });

  final double latitude;
  final double longitude;
  final FireKind kind;

  /// The smoke slider: 1 (low) .. 5 (high). A triage choice, not a
  /// measurement.
  final int smokeIntensity;

  /// User's estimate of how long the burning has been going (0 = just
  /// started), from [FireDurationOption].
  final double durationHours;

  /// Optional free text, at most [maxNotesLength] characters.
  final String? notes;

  /// Client-generated id for idempotent resubmission: a retry must update
  /// nothing. The UI generates one per draft session.
  final String? clientReportId;

  static const maxNotesLength = 280;
  static const minIntensity = 1;
  static const maxIntensity = 5;

  static FireReportDraft create({
    required double latitude,
    required double longitude,
    required FireKind kind,
    required int smokeIntensity,
    required double durationHours,
    String? notes,
    String? clientReportId,
  }) {
    final trimmedNotes = notes?.trim();
    final note = (trimmedNotes == null || trimmedNotes.isEmpty)
        ? null
        : trimmedNotes;
    final clientId = clientReportId?.trim();

    if (latitude < -90 || latitude > 90) {
      throw ArgumentError.value(latitude, 'latitude', 'must be within [-90, 90]');
    }
    if (longitude < -180 || longitude > 180) {
      throw ArgumentError.value(
          longitude, 'longitude', 'must be within [-180, 180]');
    }
    if (smokeIntensity < minIntensity || smokeIntensity > maxIntensity) {
      throw ArgumentError.value(
        smokeIntensity,
        'smokeIntensity',
        'must be between $minIntensity and $maxIntensity',
      );
    }
    if (durationHours < 0 || durationHours > 24) {
      throw ArgumentError.value(
          durationHours, 'durationHours', 'must be within [0, 24]');
    }
    if (note != null && note.length > maxNotesLength) {
      throw ArgumentError.value(
          note, 'notes', 'must be at most $maxNotesLength characters');
    }
    if (clientId != null && clientId.isEmpty) {
      throw ArgumentError.value(
          clientReportId, 'clientReportId', 'must not be blank when given');
    }

    return FireReportDraft(
      latitude: latitude,
      longitude: longitude,
      kind: kind,
      smokeIntensity: smokeIntensity,
      durationHours: durationHours,
      notes: note,
      clientReportId: clientId,
    );
  }
}

/// A stored report as the backend returned it.
class FireReport {
  const FireReport({
    required this.id,
    required this.h3Cell,
    required this.latitude,
    required this.longitude,
    required this.kind,
    required this.smokeIntensity,
    required this.durationHours,
    required this.reportedAt,
    this.notes,
    this.clientReportId,
  });

  final int id;

  /// The H3 cell the backend snapped this report to at write time.
  final String h3Cell;
  final double latitude;
  final double longitude;
  final FireKind kind;
  final int smokeIntensity;
  final double durationHours;
  final DateTime reportedAt;
  final String? notes;
  final String? clientReportId;

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is FireReport &&
          id == other.id &&
          h3Cell == other.h3Cell &&
          latitude == other.latitude &&
          longitude == other.longitude &&
          kind == other.kind &&
          smokeIntensity == other.smokeIntensity &&
          durationHours == other.durationHours &&
          reportedAt == other.reportedAt &&
          notes == other.notes &&
          clientReportId == other.clientReportId;

  @override
  int get hashCode => Object.hash(
        id,
        h3Cell,
        latitude,
        longitude,
        kind,
        smokeIntensity,
        durationHours,
        reportedAt,
        notes,
        clientReportId,
      );

  @override
  String toString() =>
      'FireReport(${kind.label}, intensity=$smokeIntensity, at=$reportedAt)';
}
