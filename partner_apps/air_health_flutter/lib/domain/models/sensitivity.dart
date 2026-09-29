/// User-selected alert sensitivity tier.
///
/// The app never assigns a tier based on health context alone —
/// the user must always confirm or override.
enum AlertSensitivity {
  standard('Standard'),
  sensitive('Sensitive'),
  high('High sensitivity'),
  custom('Custom / clinician configured');

  const AlertSensitivity(this.label);
  final String label;
}

/// Optional health context the user may share during onboarding.
///
/// This is NOT a diagnosis. It only informs which sensitivity tier
/// the app suggests — the user always has the final say.
enum UserHealthContext {
  none('No known respiratory sensitivity'),
  asthma('Asthma'),
  copd('Chronic bronchitis / COPD'),
  oncologyResp('Lung cancer / oncology-related respiratory sensitivity'),
  otherResp('Other respiratory condition'),
  cardio('Cardiovascular sensitivity'),
  preferNotToSay('Prefer not to say');

  const UserHealthContext(this.label);
  final String label;
}

/// Disease severity for patients with diagnosed or known respiratory conditions.
///
/// Determines the AQI rise threshold and warning levels for patient alerts.
enum DiseaseSeverity {
  mild('Mild', 'Occasional symptoms, alerts trigger at moderate AQI rises'),
  moderate('Moderate', 'Frequent symptoms, alerts trigger at early AQI rises'),
  severe('Severe', 'Severe condition, alerts trigger at low threshold and subtle AQI rises');

  const DiseaseSeverity(this.label, this.description);
  final String label;
  final String description;

  static DiseaseSeverity fromName(String? name) =>
      DiseaseSeverity.values.firstWhere(
        (e) => e.name == name,
        orElse: () => DiseaseSeverity.moderate,
      );
}
