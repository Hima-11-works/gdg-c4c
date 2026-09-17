import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/material.dart';

import '../../core/formatters.dart';
import '../../domain/models/models.dart';
import '../app_colors.dart';
import '../app_radius.dart';
import '../app_spacing.dart';
import '../app_typography.dart';

/// 12-hour AQI forecast chart.
///
/// Shows current AQI as the starting point, forecast line, CPCB
/// category background bands, time labels, and a marker for the
/// first category transition ("worsening point"). If an approaching
/// pollution event is present, its expected arrival is marked.
///
/// Answers: "When is air quality expected to worsen?"
/// Not a technical data-science graph — minimal decoration, readable
/// labels, accessible fallback text.
class ForecastChart extends StatelessWidget {
  const ForecastChart({
    super.key,
    required this.currentAqi,
    required this.forecast,
    this.event,
  });

  final int currentAqi;
  final List<ForecastPoint> forecast;
  final PollutionEvent? event;

  @override
  Widget build(BuildContext context) {
    if (forecast.isEmpty) {
      return _EmptyForecast(currentAqi: currentAqi);
    }

    // Build data points: index 0 = now (current AQI), 1..n = forecast hours.
    final spots = <FlSpot>[];
    spots.add(FlSpot(0, currentAqi.toDouble()));
    for (var i = 0; i < forecast.length; i++) {
      spots.add(FlSpot((i + 1).toDouble(), forecast[i].aqiCpcb.toDouble()));
    }

    // Find the first category transition (worsening point).
    final currentCat = CpcbCategory.fromAqi(currentAqi);
    int? worseningIndex;
    for (var i = 0; i < forecast.length; i++) {
      if (forecast[i].category.index > currentCat.index) {
        worseningIndex = i + 1; // +1 because index 0 is "now"
        break;
      }
    }

    // Find approaching event time index.
    int? eventIndex;
    if (event != null) {
      final eventHour = event!.expectedArrivalAt
          .difference(DateTime.now())
          .inHours
          .clamp(0, forecast.length);
      if (eventHour > 0 && eventHour <= forecast.length) {
        eventIndex = eventHour;
      }
    }

    final maxY = spots.map((s) => s.y).reduce((a, b) => a > b ? a : b);
    final chartMax = (maxY * 1.2).clamp(300, 500).toDouble();

    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: AppSpacing.xl),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // ── Chart ───────────────────────────────────────────────
          Semantics(
            label: _accessibilitySummary(),
            child: SizedBox(
              height: 200,
              child: LineChart(
                _buildChartData(spots, chartMax, worseningIndex, eventIndex),
                duration: Duration.zero, // no animation on data change
              ),
            ),
          ),

          const SizedBox(height: AppSpacing.md),

          // ── Worsening summary ───────────────────────────────────
          if (worseningIndex != null)
            _WorseningSummary(
              point: forecast[worseningIndex - 1],
            ),
        ],
      ),
    );
  }

  LineChartData _buildChartData(
    List<FlSpot> spots,
    double chartMax,
    int? worseningIndex,
    int? eventIndex,
  ) {
    return LineChartData(
      minY: 0,
      maxY: chartMax,
      minX: 0,
      maxX: 12,

      // CPCB category background bands.
      rangeAnnotations: RangeAnnotations(
        verticalRangeAnnotations: [],
        horizontalRangeAnnotations: [
          _band(0, 50, AppColors.aqiGood.withValues(alpha: 0.06)),
          _band(50, 100, AppColors.aqiSatisfactory.withValues(alpha: 0.06)),
          _band(100, 200, AppColors.aqiModerate.withValues(alpha: 0.06)),
          _band(200, 300, AppColors.aqiPoor.withValues(alpha: 0.06)),
          _band(300, 400, AppColors.aqiVeryPoor.withValues(alpha: 0.06)),
          _band(400, chartMax, AppColors.aqiSevere.withValues(alpha: 0.06)),
        ],
      ),

      // Category threshold lines.
      extraLinesData: ExtraLinesData(
        horizontalLines: [
          _thresholdLine(50, AppColors.aqiGood),
          _thresholdLine(100, AppColors.aqiSatisfactory),
          _thresholdLine(200, AppColors.aqiModerate),
          _thresholdLine(300, AppColors.aqiPoor),
        ],
        verticalLines: [
          if (worseningIndex != null)
            VerticalLine(
              x: worseningIndex.toDouble(),
              color: AppColors.warning.withValues(alpha: 0.5),
              strokeWidth: 1,
              dashArray: [4, 4],
            ),
          if (eventIndex != null)
            VerticalLine(
              x: eventIndex.toDouble(),
              color: AppColors.error.withValues(alpha: 0.5),
              strokeWidth: 1,
              dashArray: [6, 3],
            ),
        ],
      ),

      lineBarsData: [
        LineChartBarData(
          spots: spots,
          isCurved: false,
          color: AppColors.info,
          barWidth: 2.5,
          dotData: FlDotData(
            show: true,
            getDotPainter: (spot, _, _, _) {
              if (spot.x == 0) {
                // Current AQI dot — larger.
                return FlDotCirclePainter(
                  radius: 5,
                  color: AppColors.forCategory(
                      CpcbCategory.fromAqi(spot.y.toInt())),
                  strokeWidth: 2,
                  strokeColor: AppColors.surface,
                );
              }
              return FlDotCirclePainter(
                radius: 3,
                color: AppColors.forCategory(
                    CpcbCategory.fromAqi(spot.y.toInt())),
                strokeWidth: 1,
                strokeColor: AppColors.surface,
              );
            },
          ),
          belowBarData: BarAreaData(
            show: true,
            color: AppColors.info.withValues(alpha: 0.08),
          ),
        ),
      ],

      titlesData: FlTitlesData(
        leftTitles: AxisTitles(
          sideTitles: SideTitles(
            showTitles: true,
            reservedSize: 36,
            interval: 100,
            getTitlesWidget: (value, _) => Text(
              value.toInt().toString(),
              style: AppTypography.labelSmall.copyWith(
                color: AppColors.onSurfaceMuted,
              ),
            ),
          ),
        ),
        bottomTitles: AxisTitles(
          sideTitles: SideTitles(
            showTitles: true,
            reservedSize: 24,
            interval: 1,
            getTitlesWidget: (value, _) {
              if (value == 0) {
                return Text('Now',
                    style: AppTypography.labelSmall.copyWith(
                      color: AppColors.onSurfaceMuted,
                    ));
              }
              if (value % 3 == 0) {
                return Text('+${value.toInt()}h',
                    style: AppTypography.labelSmall.copyWith(
                      color: AppColors.onSurfaceMuted,
                    ));
              }
              return const SizedBox.shrink();
            },
          ),
        ),
        topTitles:
            const AxisTitles(sideTitles: SideTitles(showTitles: false)),
        rightTitles:
            const AxisTitles(sideTitles: SideTitles(showTitles: false)),
      ),

      gridData: FlGridData(
        show: true,
        drawVerticalLine: false,
        horizontalInterval: 100,
        getDrawingHorizontalLine: (_) => FlLine(
          color: AppColors.outlineVariant.withValues(alpha: 0.5),
          strokeWidth: 0.5,
        ),
      ),

      borderData: FlBorderData(show: false),

      lineTouchData: LineTouchData(
        touchTooltipData: LineTouchTooltipData(
          getTooltipItems: (touchedSpots) {
            return touchedSpots.map((spot) {
              final aqi = spot.y.toInt();
              final cat = CpcbCategory.fromAqi(aqi);
              final label =
                  spot.x == 0 ? 'Now' : '+${spot.x.toInt()}h';
              return LineTooltipItem(
                '$label: $aqi\n${cat.label}',
                AppTypography.labelSmall.copyWith(
                  color: AppColors.onSurface,
                ),
              );
            }).toList();
          },
        ),
      ),
    );
  }

  HorizontalRangeAnnotation _band(
      double from, double to, Color color) {
    return HorizontalRangeAnnotation(y1: from, y2: to, color: color);
  }

  HorizontalLine _thresholdLine(double y, Color color) {
    return HorizontalLine(
      y: y,
      color: color.withValues(alpha: 0.2),
      strokeWidth: 0.5,
      dashArray: [4, 4],
      label: HorizontalLineLabel(
        show: true,
        alignment: Alignment.topRight,
        style: AppTypography.labelSmall.copyWith(
          color: color.withValues(alpha: 0.5),
        ),
        labelResolver: (_) => CpcbCategory.fromAqi(y.toInt()).label,
      ),
    );
  }

  String _accessibilitySummary() {
    final cat = CpcbCategory.fromAqi(currentAqi);
    final buffer = StringBuffer()
      ..write('Current AQI $currentAqi, ${cat.label}. ');

    if (forecast.isNotEmpty) {
      final peak =
          forecast.reduce((a, b) => a.aqiCpcb > b.aqiCpcb ? a : b);
      buffer.write(
          'Over the next 12 hours, AQI is expected to reach '
          'a peak of ${peak.aqiCpcb} (${peak.category.label}). ');
      final worstCat = forecast
          .map((f) => f.category)
          .reduce((a, b) => a.index > b.index ? a : b);
      if (worstCat.index > cat.index) {
        buffer.write('Conditions expected to reach ${worstCat.label}. ');
      }
    }

    if (event != null) {
      buffer.write(
          'Pollution from ${event!.sourceArea} expected to arrive '
          'around ${Formatters.time(event!.expectedArrivalAt)}. ');
    }

    return buffer.toString();
  }
}

class _WorseningSummary extends StatelessWidget {
  const _WorseningSummary({required this.point});

  final ForecastPoint point;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.lg,
        vertical: AppSpacing.md,
      ),
      decoration: BoxDecoration(
        color: AppColors.warning.withValues(alpha: 0.08),
        borderRadius: AppRadius.smAll,
      ),
      child: Row(
        children: [
          const Icon(Icons.trending_up, size: 16, color: AppColors.warning),
          const SizedBox(width: AppSpacing.md),
          Expanded(
            child: Text(
              'Expected to reach ${point.category.label} '
              'around ${Formatters.time(point.at)}',
              style: AppTypography.bodySmall.copyWith(
                color: AppColors.warning,
                fontWeight: FontWeight.w500,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _EmptyForecast extends StatelessWidget {
  const _EmptyForecast({required this.currentAqi});

  final int currentAqi;

  @override
  Widget build(BuildContext context) {
    final cat = CpcbCategory.fromAqi(currentAqi);
    return Padding(
      padding: AppSpacing.horizontalXl,
      child: Container(
        padding: AppSpacing.allXl,
        decoration: BoxDecoration(
          color: AppColors.surfaceDim,
          borderRadius: AppRadius.smAll,
        ),
        child: Column(
          children: [
            Text(
              'Current AQI: $currentAqi (${cat.label})',
              style: AppTypography.titleMedium,
            ),
            const SizedBox(height: AppSpacing.md),
            Text(
              'Forecast data is not available for this location right now.',
              style: AppTypography.bodySmall.copyWith(
                color: AppColors.onSurfaceMuted,
              ),
              textAlign: TextAlign.center,
            ),
          ],
        ),
      ),
    );
  }
}
