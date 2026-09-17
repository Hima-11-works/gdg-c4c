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
