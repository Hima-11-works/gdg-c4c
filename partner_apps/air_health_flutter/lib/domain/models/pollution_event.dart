/// A pollution event approaching the user's area.
class PollutionEvent {
  const PollutionEvent({
    required this.id,
    required this.sourceArea,
    required this.expectedArrivalAt,
    required this.peakAqiEstimate,
    required this.confidence,
    required this.description,
  });

  final String id;
  final String sourceArea;
  final DateTime expectedArrivalAt;
  final int peakAqiEstimate;
  final double confidence;
  final String description;
}
