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
/// Not a status the report itself reports: the report response carries no
/// verification field. What does carry one is the attached evidence
/// (POST /api/v1/reports/{id}/evidence), whose record starts `unverified` and
/// can be moderated later - see `report_evidence.dart`. So this stays the
/// label for the *report*, and the evidence carries its own.
class CitizenReportVerification {
  const CitizenReportVerification._();

  static const label = 'Unverified';
  static const detail = 'resident submitted, not a measurement';
  static const badge = '$label — $detail';
  static const tooltip =
      'Submitted by a resident, not a sensor or a satellite. The report itself '
      'carries no verification status; an attached photo or sensor reading '
      'carries its own, and it starts unverified too.';

  /// What the report sheet says about the photo and the reading: both are
  /// transmitted now, as evidence attached to the report.
  static const evidenceUploadDetail =
      'Sent to the backend as unverified evidence attached to your report, once '
      'the report is stored.';
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
