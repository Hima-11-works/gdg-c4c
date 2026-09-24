/// Citizen intake evidence: the photo and/or the local sensor reading a
/// resident attaches to a report they have already created
/// (POST /api/v1/reports/{id}/evidence).
///
/// Mirrors docs/api/citizen-intake.md. The separation the backend enforces is
/// the reason this is its own type: a citizen reading is stored only on the
/// evidence record, never as a station observation, and every record starts
/// `unverified`.
library;

/// The pollutants the backend accepts for a citizen reading
/// (CITIZEN_SENSOR_POLLUTANTS).
enum SensorPollutant {
  pm25('pm25', 'PM2.5'),
  pm10('pm10', 'PM10');

  const SensorPollutant(this.wireValue, this.label);

  final String wireValue;
  final String label;
}

/// Moderation state of an evidence record. Nothing in the intake path promotes
/// a record by itself, so a fresh upload is always [unverified].
enum EvidenceVerificationStatus {
  unverified('unverified', 'Unverified', 'stored as submitted; nothing has checked it'),
  pending('pending', 'Pending review', 'queued for a moderator to review'),
  verified('verified', 'Verified', 'a moderator confirmed this evidence'),
  rejected('rejected', 'Rejected', 'a moderator rejected this evidence');

  const EvidenceVerificationStatus(this.wireValue, this.label, this.detail);

  final String wireValue;
  final String label;
  final String detail;

  /// Unknown values fall back to [unverified] rather than throwing: a state
  /// this build has never heard of is still, as far as it can honestly say,
  /// not something it may present as verified.
  static EvidenceVerificationStatus fromWire(String? value) {
    for (final status in EvidenceVerificationStatus.values) {
      if (status.wireValue == value) return status;
    }
    return EvidenceVerificationStatus.unverified;
  }
}

/// True of every evidence record regardless of state.
const String kEvidenceNeverAMeasurement =
    'A citizen reading is stored only on this evidence record. It never becomes '
    'a station observation and never feeds the pollution model.';

/// A reading from a monitor the resident owns, ready to be attached.
class CitizenSensorEvidence {
  const CitizenSensorEvidence({
    required this.pollutant,
    required this.value,
    required this.unit,
    required this.measuredAt,
  });

  final SensorPollutant pollutant;
  final double value;
  final String unit;

  /// When the reading was taken, not when it was uploaded. The backend refuses
  /// anything older than [maxAge] or further into the future than
  /// [maxFutureSkew].
  final DateTime measuredAt;

  /// Mirrors CITIZEN_SENSOR_MAX_AGE_HOURS.
  static const Duration maxAge = Duration(hours: 72);

  /// Mirrors CITIZEN_SENSOR_MAX_FUTURE_SKEW_SECONDS.
  static const Duration maxFutureSkew = Duration(seconds: 300);

  /// RFC 3339 with an offset, which is what the backend parses.
  String get measuredAtRfc3339 => measuredAt.toUtc().toIso8601String();

  /// Null unless the text parses to a finite, non-negative number, which is
  /// what the form uses to decide whether a reading can be attached at all.
  static CitizenSensorEvidence? tryCreate({
    required String rawValue,
    required SensorPollutant pollutant,
    required String unit,
    required DateTime measuredAt,
    DateTime? now,
  }) {
    final parsed = double.tryParse(rawValue.trim());
    if (parsed == null || !parsed.isFinite || parsed < 0) return null;
    if (unit.trim().isEmpty) return null;
    final reference = now ?? DateTime.now();
    final age = reference.difference(measuredAt);
    if (age > maxAge) return null;
    if (age < -maxFutureSkew) return null;
    return CitizenSensorEvidence(
      pollutant: pollutant,
      value: parsed,
      unit: unit.trim(),
      measuredAt: measuredAt,
    );
  }

  /// "87.5 µg/m³" — whole numbers stay whole.
  String get display {
    final whole = value == value.roundToDouble();
    return '${whole ? value.toStringAsFixed(0) : value.toStringAsFixed(1)} $unit';
  }

  /// The multipart field set the backend expects, all four parts together.
  Map<String, String> toFormFields() => {
        'sensor_pollutant': pollutant.wireValue,
        'sensor_value': value.toString(),
        'sensor_unit': unit,
        'sensor_measured_at': measuredAtRfc3339,
      };

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is CitizenSensorEvidence &&
          pollutant == other.pollutant &&
          value == other.value &&
          unit == other.unit &&
          measuredAt == other.measuredAt;

  @override
  int get hashCode => Object.hash(pollutant, value, unit, measuredAt);

  @override
  String toString() => 'CitizenSensorEvidence($display at $measuredAt)';
}

/// The stored photo on an evidence record.
class ReportEvidenceMedia {
  const ReportEvidenceMedia({
    required this.contentType,
    required this.byteSize,
    required this.sha256,
    required this.url,
    required this.isPlaceholder,
  });

  final String contentType;
  final int byteSize;
  final String sha256;

  /// Relative to the API origin.
  final String url;
  final bool isPlaceholder;

  static ReportEvidenceMedia fromJson(Map<String, dynamic> json) => ReportEvidenceMedia(
        contentType: json['content_type'] as String,
        byteSize: (json['byte_size'] as num).toInt(),
        sha256: json['sha256'] as String,
        url: json['url'] as String,
        isPlaceholder: json['is_placeholder'] as bool? ?? false,
      );
}

/// A citizen reading as the backend stored it. [source] is always `citizen` and
/// [verified] is false for every non-verified state, so this can never be
/// mistaken for a trusted observation.
class ReportEvidenceSensor {
  const ReportEvidenceSensor({
    required this.pollutant,
    required this.value,
    required this.unit,
    required this.measuredAt,
    required this.latitude,
    required this.longitude,
    required this.source,
    required this.verified,
  });

  final String pollutant;
  final double value;
  final String unit;
  final DateTime measuredAt;
  final double latitude;
  final double longitude;
  final String source;
  final bool verified;

  static ReportEvidenceSensor fromJson(Map<String, dynamic> json) => ReportEvidenceSensor(
        pollutant: json['pollutant'] as String,
        value: (json['value'] as num).toDouble(),
        unit: json['unit'] as String,
        measuredAt: DateTime.parse(json['measured_at'] as String),
        latitude: (json['latitude'] as num).toDouble(),
        longitude: (json['longitude'] as num).toDouble(),
        source: json['source'] as String,
        verified: json['verified'] as bool? ?? false,
      );
}

/// The evidence record for a report, as POST/GET
/// /api/v1/reports/{id}/evidence returns it. One report has at most one, so
/// this is a sub-resource of the report id rather than a collection.
class ReportEvidence {
  const ReportEvidence({
    required this.id,
    required this.reportId,
    required this.verificationStatus,
    required this.media,
    required this.sensor,
    required this.notes,
    required this.submittedAt,
    this.clientReportId,
  });

  final int id;
  final int reportId;
  final String? clientReportId;
  final EvidenceVerificationStatus verificationStatus;
  final ReportEvidenceMedia? media;
  final ReportEvidenceSensor? sensor;
  final String? notes;
  final DateTime submittedAt;

  bool get hasPhoto => media != null;
  bool get hasSensor => sensor != null;

  static ReportEvidence fromJson(Map<String, dynamic> json) {
    final media = json['media'];
    final sensor = json['sensor'];
    return ReportEvidence(
      id: (json['id'] as num).toInt(),
      reportId: (json['report_id'] as num).toInt(),
      clientReportId: json['client_report_id'] as String?,
      verificationStatus:
          EvidenceVerificationStatus.fromWire(json['verification_status'] as String?),
      media: media is Map<String, dynamic> ? ReportEvidenceMedia.fromJson(media) : null,
      sensor: sensor is Map<String, dynamic> ? ReportEvidenceSensor.fromJson(sensor) : null,
      notes: json['notes'] as String?,
      submittedAt: DateTime.parse(json['submitted_at'] as String),
    );
  }
}
