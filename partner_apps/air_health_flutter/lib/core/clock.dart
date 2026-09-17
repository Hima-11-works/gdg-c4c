/// Injectable clock — real code uses [systemClock], tests use [fixedClock].
abstract class Clock {
  DateTime now();
}

class SystemClock implements Clock {
  const SystemClock();

  @override
  DateTime now() => DateTime.now();
}

class FixedClock implements Clock {
  const FixedClock(this._now);
  final DateTime _now;

  @override
  DateTime now() => _now;
}

/// Default clock used by the app (overridden in tests).
const Clock systemClock = SystemClock();
