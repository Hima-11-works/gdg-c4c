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

  /// Time until the event arrives.
  Duration get leadTime => expectedArrivalAt.difference(DateTime.now());

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is PollutionEvent &&
          id == other.id &&
          sourceArea == other.sourceArea &&
          expectedArrivalAt == other.expectedArrivalAt &&
          peakAqiEstimate == other.peakAqiEstimate &&
          confidence == other.confidence &&
          description == other.description;

  @override
  int get hashCode => Object.hash(
        id,
        sourceArea,
        expectedArrivalAt,
        peakAqiEstimate,
        confidence,
        description,
      );

  @override
  String toString() =>
      'PollutionEvent($sourceArea, peak=$peakAqiEstimate, arrival=$expectedArrivalAt)';
}
