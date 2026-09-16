import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/formatters.dart';
import '../../domain/models/models.dart';
import '../../providers/home_providers.dart';
import '../../theme/app_colors.dart';
import '../../theme/app_spacing.dart';
import '../../theme/app_typography.dart';
import '../../theme/widgets/widgets.dart';

/// Nearby areas screen — shows nearby locations with lower expected
/// pollution, sortable by cleaner-now / cleaner-soon / distance.
class NearbyScreen extends ConsumerWidget {
  const NearbyScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final areas = ref.watch(nearbyAreasProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('Nearby Areas')),
      body: areas.when(
        loading: () => const LoadingState(),
        error: (e, _) => ErrorState(
          title: 'Could not load nearby areas',
          message: e.toString(),
          onRetry: () => ref.invalidate(nearbyAreasProvider),
        ),
        data: (areas) {
          if (areas.isEmpty) {
            return const EmptyState(
              icon: Icons.location_on_outlined,
              title: 'No nearby areas',
              message: 'Nearby pollution data will appear here when available.',
            );
          }
          return ListView.builder(
            padding: const EdgeInsets.only(bottom: AppSpacing.xxxxl),
            itemCount: areas.length,
            itemBuilder: (_, i) => _NearbyAreaTile(area: areas[i]),
          );
        },
      ),
    );
  }
}

class _NearbyAreaTile extends StatelessWidget {
  const _NearbyAreaTile({required this.area});

  final NearbyArea area;

  @override
  Widget build(BuildContext context) {
    final color = AppColors.forCategory(CpcbCategory.fromAqi(area.aqiNow));
    final trendChip = switch (area.trend) {
      AreaTrend.improving => const TrendChip.improving(),
      AreaTrend.stable => const TrendChip.stable(),
      AreaTrend.worsening => const TrendChip.worsening(),
    };

    return AppCard(
      margin: const EdgeInsets.symmetric(
        horizontal: AppSpacing.xl,
        vertical: AppSpacing.sm,
      ),
      child: Row(
        children: [
          // AQI badge.
          Container(
            width: 56,
            height: 56,
            alignment: Alignment.center,
            decoration: BoxDecoration(
              color: color.withValues(alpha: 0.12),
              borderRadius: BorderRadius.circular(10),
            ),
            child: Text(
              '${area.aqiNow}',
              style: AppTypography.headlineSmall.copyWith(color: color),
            ),
          ),
          const SizedBox(width: AppSpacing.xl),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(area.name, style: AppTypography.titleMedium),
                const SizedBox(height: AppSpacing.xs),
                Text(
                  Formatters.distance(area.distanceKm),
                  style: AppTypography.bodySmall.copyWith(
                    color: AppColors.onSurfaceMuted,
                  ),
                ),
              ],
            ),
          ),
          trendChip,
        ],
      ),
    );
  }
}
