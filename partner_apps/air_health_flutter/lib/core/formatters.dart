import 'package:intl/intl.dart';

/// Centralised formatters — one place for date/time/number display.
abstract final class Formatters {
  static final DateFormat _time = DateFormat.jm(); // e.g. 6:30 PM
  static final DateFormat _dateTime = DateFormat('MMM d, h:mm a');
  static final DateFormat _hourMin = DateFormat('h:mm a');

  static String time(DateTime dt) => _time.format(dt);
  static String dateTime(DateTime dt) => _dateTime.format(dt);
  static String hourMin(DateTime dt) => _hourMin.format(dt);

  /// "in 90 minutes" / "45 min ago".
  static String relativeDuration(Duration d) {
    if (d.isNegative) {
      final abs = d.abs();
      if (abs.inMinutes < 60) return '${abs.inMinutes} min ago';
      return '${abs.inHours}h ago';
    }
    if (d.inMinutes < 60) return 'in ${d.inMinutes} min';
    return 'in ${d.inHours}h';
  }

  /// Format a distance in km.
  static String distance(double km) {
    if (km < 1) return '${(km * 1000).round()} m';
    return '${km.toStringAsFixed(1)} km';
  }
}
