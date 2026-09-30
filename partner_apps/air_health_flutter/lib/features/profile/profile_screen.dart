import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/app_info.dart';
import '../../core/formatters.dart';
import '../../domain/models/models.dart';
import '../../domain/sensitivity_rules.dart';
import '../../notifications/notification_service.dart';
import '../../notifications/scheduled_alarm.dart';
import '../../providers/alert_providers.dart';
import '../../providers/location_providers.dart';
import '../../providers/prefs_providers.dart';
import '../../providers/profile_providers.dart';
import '../../services/location_service.dart';
import '../../theme/app_spacing.dart';
import '../../theme/app_typography.dart';

/// Settings screen — all user-facing preferences in one place.
///
/// Sections: Location, Notifications, Alert Sensitivity, Health Profile,
/// Privacy, Reset. No technical model settings exposed.
class ProfileScreen extends ConsumerWidget {
  const ProfileScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final profileAsync = ref.watch(userProfileProvider);
    final cs = Theme.of(context).colorScheme;

    return Scaffold(
      appBar: AppBar(title: const Text('Settings')),
      body: profileAsync.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => Center(
          child: Text('Could not load settings: $e'),
        ),
        data: (profile) {
          final prefs = profile?.preferences ?? const UserAlertPreferences();
          final location = ref.watch(resolvedLocationProvider);
          final notificationPermission =
              ref.watch(notificationPermissionProvider);
          final exactAsync = ref.watch(exactAlarmsProvider);
          final upcomingAsync = ref.watch(upcomingAlarmsProvider);
          // The exact-alarm row only exists on Android; iOS delivers local
          // alarms without an equivalent grant.
          final usesAndroid =
              !kIsWeb && defaultTargetPlatform == TargetPlatform.android;
          return ListView(
            padding: const EdgeInsets.only(bottom: AppSpacing.xxxxl),
            children: [
              // ── Location ──────────────────────────────────────────
              _SectionHeader('Location'),
              _SettingsTile(
                icon: Icons.location_on_outlined,
                title: 'Current location',
                subtitle: location.isFallback
                    ? 'Fallback · ${location.label ?? "Bhubaneswar"}'
                    : (location.label ?? 'Current position'),
                onTap: () => _manageLocationPermission(context, ref),
              ),
              _SettingsTile(
                icon: Icons.location_searching,
                title: 'Location permission',
                subtitle: 'Approximate location — used for nearby data',
                onTap: () => _manageLocationPermission(context, ref),
              ),

              // ── Notifications ─────────────────────────────────────
              _SectionHeader('Notifications'),
              _SwitchTile(
                icon: Icons.notifications_outlined,
                title: 'Alert notifications',
                subtitle: 'Receive alerts when air quality changes',
                value: prefs.alertsEnabled,
                onChanged: (v) => ref
                    .read(userProfileProvider.notifier)
                    .updateFields(preferences: prefs.copyWith(alertsEnabled: v)),
              ),
              _SettingsTile(
                icon: Icons.notifications_active_outlined,
                title: 'Notification permission',
                subtitle: notificationPermission.when(
                  data: (granted) => granted
                      ? 'Granted — on-device alerts can appear'
                      : 'Not granted — alerts stay in the app only',
                  error: (_, _) => 'Could not check system permission',
                  loading: () => 'Checking system permission…',
                ),
                onTap: () => _manageNotificationPermission(context, ref),
              ),
              _TimeRangeTile(
                icon: Icons.do_not_disturb_on_outlined,
                title: 'Quiet hours',
                subtitle: prefs.hasQuietHours
                    ? 'Urgent alerts still come through'
                    : 'No notifications during this time',
                startHour: prefs.quietHoursStart?.hour,
                endHour: prefs.quietHoursEnd?.hour,
                onTap: () => _pickQuietHours(context, ref, prefs),
              ),
              _SwitchTile(
                icon: Icons.alarm,
                title: 'Forecast alarms',
                subtitle: 'Ring at predicted air-quality changes, even '
                    'when the app is closed',
                value: prefs.forecastAlarmsEnabled,
                onChanged: (v) async {
                  HapticFeedback.selectionClick();
                  await ref
                      .read(userProfileProvider.notifier)
                      .updateFields(
                        preferences: prefs.copyWith(forecastAlarmsEnabled: v),
                      );
                  if (!v) {
                    // Stop pending OS alarms immediately, so none can ring
                    // before the next evaluation cycle.
                    await ref
                        .read(forecastAlarmSchedulerProvider)
                        .cancelAll();
                  } else {
                    // Reconcile now, so the next alarm is registered without
                    // waiting for the refresh cadence.
                    await ref
                        .read(alertCoordinatorProvider)
                        .refreshAndEvaluate();
                  }
                  ref.invalidate(upcomingAlarmsProvider);
                },
              ),
              _SettingsTile(
                icon: Icons.alarm_on,
                title: 'Alarm lead time',
                subtitle: _alarmLeadLabel(prefs),
                onTap: () => _pickAlarmLead(context, ref, prefs),
              ),
              _SettingsTile(
                icon: Icons.notifications_active,
                title: 'Upcoming alarms',
                subtitle: _upcomingAlarmsLabel(upcomingAsync),
                // The list is a snapshot taken at schedule time; tapping
                // re-reads it.
                onTap: () => ref.invalidate(upcomingAlarmsProvider),
              ),
              if (usesAndroid)
                _SettingsTile(
                  icon: Icons.access_alarms,
                  title: 'Exact alarms',
                  subtitle: exactAsync.maybeWhen(
                    data: (granted) => granted
                        ? 'Granted — alarms fire on time'
                        : 'Not granted — alarms may ring up to 15 min late',
                    orElse: () => 'Checking…',
                  ),
                  onTap: () => _manageExactAlarms(context, ref),
                ),
              _SettingsTile(
                icon: Icons.volume_up_outlined,
                title: '5-second alert sound',
                subtitle: 'Audible warning tone & haptics for rising AQI alerts',
                onTap: () => _testAlertSound(context, ref),
              ),

              // ── Alert Sensitivity ─────────────────────────────────
              _SectionHeader('Alert Sensitivity'),
              _SettingsTile(
                icon: Icons.tune,
                title: 'Sensitivity level',
                subtitle: profile?.sensitivity.label ?? 'Standard',
                onTap: () => _editSensitivity(context, ref, profile),
              ),
              _SettingsTile(
                icon: Icons.access_time,
                title: 'Forecast warning lead time',
                subtitle: _leadTimeLabel(profile),
                onTap: () => _pickLeadTime(context, ref, profile),
              ),
              _SwitchTile(
                icon: Icons.trending_down,
                title: 'Recovery alerts',
                subtitle: 'Notify when air quality improves',
                value: prefs.recoveryAlertsEnabled,
                onChanged: (v) => ref
                    .read(userProfileProvider.notifier)
                    .updateFields(preferences: prefs.copyWith(recoveryAlertsEnabled: v)),
              ),

              // ── Health Profile ────────────────────────────────────
              _SectionHeader('Health Profile'),
              _SettingsTile(
                icon: Icons.health_and_safety_outlined,
                title: 'Health context',
                subtitle: profile?.healthContext.label ?? 'None',
                onTap: () => _editHealthContext(context, ref, profile),
              ),
              if (profile?.isPatient == true)
                _SettingsTile(
                  icon: Icons.speed_outlined,
                  title: 'Condition severity',
                  subtitle: profile?.effectiveDiseaseSeverity.label ?? 'Moderate',
                  onTap: () => _editDiseaseSeverity(context, ref, profile),
                ),
              Container(
                margin: const EdgeInsets.symmetric(
                  horizontal: AppSpacing.xl,
                  vertical: AppSpacing.sm,
                ),
                padding: const EdgeInsets.all(AppSpacing.lg),
                decoration: BoxDecoration(
                  color: cs.surfaceContainerHighest,
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Row(
                  children: [
                    Icon(Icons.shield_outlined, size: 16, color: cs.onSurfaceVariant),
                    const SizedBox(width: AppSpacing.md),
                    Expanded(
                      child: Text(
                        'Health information stays on your device only. '
                        'It is never transmitted or logged.',
                        style: AppTypography.bodySmall.copyWith(
                          color: cs.onSurfaceVariant,
                        ),
                      ),
                    ),
                  ],
                ),
              ),

              // ── Privacy ───────────────────────────────────────────
              _SectionHeader('Privacy'),
              _SettingsTile(
                icon: Icons.lock_outline,
                title: 'Data & privacy',
                subtitle: 'All data stored on-device only',
                onTap: () => _showPrivacyInfo(context),
              ),
              _SettingsTile(
                icon: Icons.delete_outline,
                title: 'Delete health profile',
                subtitle: 'Remove all stored health information',
                isDestructive: true,
                onTap: () => _confirmDeleteProfile(context, ref),
              ),

              // ── Reset ─────────────────────────────────────────────
              _SectionHeader('App'),
              _SettingsTile(
                icon: Icons.restore,
                title: 'Reset app',
                subtitle: 'Clear all data and start over',
                isDestructive: true,
                onTap: () => _confirmReset(context, ref),
              ),
              _SettingsTile(
                icon: Icons.info_outline,
                title: 'About',
                subtitle: '${AppInfo.name} v${AppInfo.version}',
                onTap: () => _showAbout(context),
              ),
            ],
          );
        },
      ),
    );
  }

  // ── Edit sensitivity ─────────────────────────────────────────────────

  void _editSensitivity(
    BuildContext context,
    WidgetRef ref,
    UserProfile? profile,
  ) {
    if (profile == null) return;
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      builder: (BuildContext sheetContext) => _SensitivityPicker(
        current: profile.sensitivity,
        onSelected: (s) {
          ref.read(userProfileProvider.notifier).updateFields(sensitivity: s);
          Navigator.pop(sheetContext);
          if (s == AlertSensitivity.custom) {
            _editCustomRules(context, ref, profile);
          }
        },
      ),
    );
  }

  // ── Edit custom sensitivity rules ────────────────────────────────────

  void _editCustomRules(
    BuildContext context,
    WidgetRef ref,
    UserProfile? profile,
  ) {
    final initial = profile?.customRules ?? const CustomSensitivityRules();
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      builder: (BuildContext sheetContext) => _CustomRulesSheet(
        initial: initial,
        onSave: (rules) {
          ref.read(userProfileProvider.notifier).updateFields(
                sensitivity: AlertSensitivity.custom,
                customRules: rules,
              );
          Navigator.pop(sheetContext);
        },
      ),
    );
  }

  // ── Quiet hours ──────────────────────────────────────────────────────

  void _pickQuietHours(
    BuildContext context,
    WidgetRef ref,
    UserAlertPreferences prefs,
  ) {
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      builder: (BuildContext sheetContext) => _QuietHoursSheet(
        initial: prefs,
        onSave: (updated) {
          ref
              .read(userProfileProvider.notifier)
              .updateFields(preferences: updated);
          Navigator.pop(sheetContext);
        },
      ),
    );
  }

  // ── Forecast warning lead time ───────────────────────────────────────

  void _pickLeadTime(
    BuildContext context,
    WidgetRef ref,
    UserProfile? profile,
  ) {
    final prefs = profile?.preferences ?? const UserAlertPreferences();
    final effective = prefs.leadTime ??
        SensitivityRules.forProfile(_toSensitivityProfile(profile))
            .leadTimePreference;
    showModalBottomSheet<void>(
      context: context,
      builder: (BuildContext sheetContext) => _LeadTimePicker(
        current: effective,
        onSelected: (d) {
          ref
              .read(userProfileProvider.notifier)
              .updateFields(preferences: prefs.copyWith(leadTime: d));
          Navigator.pop(sheetContext);
        },
      ),
    );
  }

  /// The lead time currently in effect: the user's override, else the
  /// sensitivity tier's default.
  static String _leadTimeLabel(UserProfile? profile) {
    final prefs = profile?.preferences ?? const UserAlertPreferences();
    final effective = prefs.leadTime ??
        SensitivityRules.forProfile(_toSensitivityProfile(profile))
            .leadTimePreference;
    return effective.inHours == 1
        ? '1 hour ahead'
        : '${effective.inHours} hours ahead';
  }

  // ── Forecast alarms ──────────────────────────────────────────────────

  static String _alarmLeadLabel(UserAlertPreferences prefs) {
    final lead = prefs.alarmLead;
    if (lead == Duration.zero) return 'At the predicted time';
    return '${_alarmLeadOptionLabel(lead)} before the change';
  }

  /// What the Settings "Upcoming alarms" row shows: the nearest scheduled
  /// alarm(s), or a quiet hint when nothing is pending.
  static String _upcomingAlarmsLabel(AsyncValue<List<ScheduledAlarm>> alarms) {
    return alarms.maybeWhen(
      data: (list) {
        if (alarms.isLoading) return 'Checking…';
        if (list.isEmpty) return 'None scheduled';
        return list
            .take(2)
            .map((a) =>
                '${a.categoryLabel} (AQI ${a.predictedAqi}) around '
                '${Formatters.time(a.fireAt)}')
            .join(' · ');
      },
      orElse: () => 'Checking…',
    );
  }

  static String _alarmLeadOptionLabel(Duration lead) {
    return switch (lead) {
      const Duration(minutes: 10) => '10 minutes',
      const Duration(minutes: 30) => '30 minutes',
      const Duration(hours: 1) => '1 hour',
      _ => '${lead.inMinutes} minutes',
    };
  }

  void _pickAlarmLead(
    BuildContext context,
    WidgetRef ref,
    UserAlertPreferences prefs,
  ) {
    showModalBottomSheet<void>(
      context: context,
      builder: (BuildContext sheetContext) => _AlarmLeadPicker(
        current: prefs.alarmLead,
        onSelected: (lead) {
          ref
              .read(userProfileProvider.notifier)
              .updateFields(preferences: prefs.copyWith(alarmLead: lead));
          Navigator.pop(sheetContext);
        },
      ),
    );
  }

  /// Ask Android (12+) for the exact-alarm grant and reflect the result.
  Future<void> _manageExactAlarms(BuildContext context, WidgetRef ref) async {
    final granted = await ref
        .read(notificationServiceProvider)
        .requestExactAlarmPermission();
    if (!context.mounted) return;
    ref.invalidate(exactAlarmsProvider);
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(
          granted
              ? 'Exact alarms enabled'
              : 'Exact alarms are off — alarms may ring up to '
                  '15 minutes late',
        ),
      ),
    );
  }

  // ── Edit health context ──────────────────────────────────────────────

  void _editHealthContext(
    BuildContext context,
    WidgetRef ref,
    UserProfile? profile,
  ) {
    if (profile == null) return;
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      builder: (BuildContext sheetContext) => _HealthContextPicker(
        current: profile.healthContext,
        onSelected: (h) {
          ref.read(userProfileProvider.notifier).updateFields(healthContext: h);
          Navigator.pop(sheetContext);
        },
      ),
    );
  }

  void _editDiseaseSeverity(
    BuildContext context,
    WidgetRef ref,
    UserProfile? profile,
  ) {
    if (profile == null) return;
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      builder: (BuildContext sheetContext) => _DiseaseSeverityPicker(
        current: profile.effectiveDiseaseSeverity,
        onSelected: (sev) {
          ref.read(userProfileProvider.notifier).updateFields(diseaseSeverity: sev);
          Navigator.pop(sheetContext);
        },
      ),
    );
  }

  void _testAlertSound(BuildContext context, WidgetRef ref) {
    final soundService = ref.read(alertSoundServiceProvider);
    soundService.play5SecondAlertSound();
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: const Text('Playing 5-second alert warning sound & haptics…'),
        duration: const Duration(seconds: 5),
        action: SnackBarAction(
          label: 'Stop',
          onPressed: () => soundService.stopAlertSound(),
        ),
      ),
    );
  }

  // ── Location permission ──────────────────────────────────────────────

  Future<void> _manageLocationPermission(
    BuildContext context,
    WidgetRef ref,
  ) async {
    final service = ref.read(locationServiceProvider);
    late final LocationPermissionStatus status;
    try {
      status = await service.checkPermission();
    } catch (_) {
      if (context.mounted) {
        _showMessage(context, 'Could not check location permission. Try again.');
      }
      return;
    }
    if (!context.mounted) return;

    if (status == LocationPermissionStatus.permanentlyDenied) {
      await service.openAppSettings();
      return;
    }
    if (status == LocationPermissionStatus.serviceDisabled) {
      await service.openLocationSettings();
      return;
    }
    if (status == LocationPermissionStatus.granted) {
      try {
        await ref.read(alertCoordinatorProvider).refreshAndEvaluate();
      } catch (_) {}
      if (context.mounted) _showMessage(context, 'Location refreshed');
      return;
    }

    // Denied (not yet determined): request it.
    final result = await service.requestAndLocate();
    if (!context.mounted) return;
    if (result is LocationSuccess) {
      try {
        await ref.read(alertCoordinatorProvider).refreshAndEvaluate();
      } catch (_) {}
      if (context.mounted) _showMessage(context, 'Location enabled and air-quality alerts refreshed');
    } else if (result is LocationPermanentlyDenied) {
      _showMessage(context, 'Location is blocked — enable it in system settings');
    } else {
      _showMessage(context, 'Location permission not granted');
    }
  }

  // ── Notification permission ──────────────────────────────────────────

  Future<void> _manageNotificationPermission(
    BuildContext context,
    WidgetRef ref,
  ) async {
    final result =
        await ref.read(notificationServiceProvider).requestPermissions();
    if (!context.mounted) return;
    ref.invalidate(notificationPermissionProvider);
    ref.invalidate(upcomingAlarmsProvider);
    try {
      await ref.read(alertCoordinatorProvider).refreshAndEvaluate();
    } catch (_) {}
    if (!context.mounted) return;
    _showMessage(
      context,
      switch (result) {
        NotificationPermissionResult.granted => 'Notifications enabled',
        NotificationPermissionResult.denied =>
          'Notification permission not granted',
        NotificationPermissionResult.permanentlyDenied =>
          'Notifications are blocked in system settings',
      },
    );
  }

  static void _showMessage(BuildContext context, String message) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(message)),
    );
  }

  // ── Privacy info ─────────────────────────────────────────────────────

  void _showPrivacyInfo(BuildContext context) {
    showDialog(
      context: context,
      builder: (BuildContext dialogContext) => AlertDialog(
        title: const Text('Data & Privacy'),
        content: const Text(
          'Your health profile and alert preferences are stored '
          'securely on your device only.\n\n'
          'They are never transmitted to any server, logged, '
          'or included in analytics.\n\n'
          'No account is required to use this app.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialogContext),
            child: const Text('OK'),
          ),
        ],
      ),
    );
  }

  // ── Delete profile ───────────────────────────────────────────────────

  void _confirmDeleteProfile(BuildContext context, WidgetRef ref) {
    showDialog(
      context: context,
      builder: (BuildContext dialogContext) => AlertDialog(
        title: const Text('Delete health profile?'),
        content: const Text(
          'This removes your health context and sensitivity '
          'settings from this device. You can re-enter them later.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialogContext),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () {
              ref.read(userProfileProvider.notifier).deleteProfile();
              Navigator.pop(dialogContext);
              ScaffoldMessenger.of(context).showSnackBar(
                const SnackBar(content: Text('Health profile deleted')),
              );
            },
            child: const Text('Delete', style: TextStyle(color: Colors.red)),
          ),
        ],
      ),
    );
  }

  // ── Reset app ────────────────────────────────────────────────────────

  void _confirmReset(BuildContext context, WidgetRef ref) {
    showDialog(
      context: context,
      builder: (BuildContext dialogContext) => AlertDialog(
        title: const Text('Reset app?'),
        content: const Text(
          'This clears all data and settings, including your '
          'health profile, onboarding state, and alert history. '
          'The app will restart from the beginning.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialogContext),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () async {
              await ref.read(prefsStoreProvider).resetAll();
              await ref.read(userProfileProvider.notifier).resetProfile();
              if (context.mounted) {
                Navigator.pop(dialogContext);
                context.go('/onboarding');
              }
            },
            child: const Text('Reset', style: TextStyle(color: Colors.red)),
          ),
        ],
      ),
    );
  }

  // ── About ────────────────────────────────────────────────────────────

  void _showAbout(BuildContext context) {
    showDialog(
      context: context,
      builder: (BuildContext dialogContext) => AlertDialog(
        title: const Text('About ${AppInfo.name}'),
        content: Text(
          '${AppInfo.tagline} app.\n\n'
          '${AppInfo.name} shows local pollution levels, short-term '
          'forecasts, and personalised alerts — so you can make '
          'informed decisions about outdoor exposure.\n\n'
          'This is an environmental awareness tool, not a '
          'medical device.\n\n'
          'Version ${AppInfo.version}',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialogContext),
            child: const Text('OK'),
          ),
        ],
      ),
    );
  }

  /// Convert a [UserProfile] (nullable) to a [UserSensitivityProfile]
  /// for [SensitivityRules.forProfile].
  static UserSensitivityProfile _toSensitivityProfile(UserProfile? p) {
    if (p == null) {
      return const UserSensitivityProfile(
        healthContext: UserHealthContext.none,
        sensitivity: AlertSensitivity.standard,
        preferences: UserAlertPreferences(),
      );
    }
    return UserSensitivityProfile(
      healthContext: p.healthContext,
      sensitivity: p.sensitivity,
      preferences: p.preferences,
      diseaseSeverity: p.diseaseSeverity,
      customRules: p.customRules,
    );
  }
}

// ── Settings tile widgets ──────────────────────────────────────────────

class _SectionHeader extends StatelessWidget {
  const _SectionHeader(this.title);
  final String title;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.fromLTRB(
          AppSpacing.xl, AppSpacing.xxl, AppSpacing.xl, AppSpacing.sm),
      child: Text(
        title.toUpperCase(),
        style: AppTypography.labelMedium.copyWith(
          color: cs.primary,
          fontWeight: FontWeight.w600,
          letterSpacing: 0.8,
        ),
      ),
    );
  }
}

class _SettingsTile extends StatelessWidget {
  const _SettingsTile({
    required this.icon,
    required this.title,
    required this.subtitle,
    required this.onTap,
    this.isDestructive = false,
  });

  final IconData icon;
  final String title;
  final String subtitle;
  final VoidCallback onTap;
  final bool isDestructive;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final titleColor = isDestructive ? Colors.red : cs.onSurface;
    final subtitleColor = isDestructive ? Colors.red.shade200 : cs.onSurfaceVariant;

    return ListTile(
      leading: Icon(icon, color: isDestructive ? Colors.red : cs.onSurfaceVariant),
      title: Text(title, style: AppTypography.titleMedium.copyWith(color: titleColor)),
      subtitle: Text(subtitle, style: AppTypography.bodySmall.copyWith(color: subtitleColor)),
      trailing: Icon(Icons.chevron_right, color: cs.onSurfaceVariant, size: 20),
      onTap: onTap,
      contentPadding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.xl,
        vertical: AppSpacing.xs,
      ),
    );
  }
}

class _SwitchTile extends StatelessWidget {
  const _SwitchTile({
    required this.icon,
    required this.title,
    required this.subtitle,
    required this.value,
    required this.onChanged,
  });

  final IconData icon;
  final String title;
  final String subtitle;
  final bool value;
  final ValueChanged<bool> onChanged;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;

    return ListTile(
      leading: Icon(icon, color: cs.onSurfaceVariant),
      title: Text(title, style: AppTypography.titleMedium.copyWith(color: cs.onSurface)),
      subtitle: Text(subtitle, style: AppTypography.bodySmall.copyWith(color: cs.onSurfaceVariant)),
      trailing: Switch(
        value: value,
        onChanged: onChanged,
      ),
      contentPadding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.xl,
        vertical: AppSpacing.xs,
      ),
    );
  }
}

class _TimeRangeTile extends StatelessWidget {
  const _TimeRangeTile({
    required this.icon,
    required this.title,
    required this.subtitle,
    required this.startHour,
    required this.endHour,
    required this.onTap,
  });

  final IconData icon;
  final String title;
  final String subtitle;
  final int? startHour;
  final int? endHour;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final timeText = startHour != null && endHour != null
        ? '${_fmt(startHour!)} – ${_fmt(endHour!)}'
        : 'Off';

    return ListTile(
      leading: Icon(icon, color: cs.onSurfaceVariant),
      title: Text(title, style: AppTypography.titleMedium.copyWith(color: cs.onSurface)),
      subtitle: Text(subtitle, style: AppTypography.bodySmall.copyWith(color: cs.onSurfaceVariant)),
      trailing: Text(
        timeText,
        style: AppTypography.bodyMedium.copyWith(color: cs.onSurfaceVariant),
      ),
      onTap: onTap,
      contentPadding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.xl,
        vertical: AppSpacing.xs,
      ),
    );
  }

  static String _fmt(int hour) {
    final h = hour % 12 == 0 ? 12 : hour % 12;
    final ap = hour < 12 ? 'AM' : 'PM';
    return '$h:00 $ap';
  }
}

// ── Pickers (bottom sheets) ───────────────────────────────────────────

class _SensitivityPicker extends StatelessWidget {
  const _SensitivityPicker({required this.current, required this.onSelected});

  final AlertSensitivity current;
  final ValueChanged<AlertSensitivity> onSelected;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;

    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(
            AppSpacing.xl, AppSpacing.xxl, AppSpacing.xl, AppSpacing.xxxxl),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Alert Sensitivity',
                style: AppTypography.headlineSmall.copyWith(color: cs.onSurface)),
            const SizedBox(height: AppSpacing.md),
            Text(
              'Choose how early you want to be notified. '
              'This does not diagnose or treat any condition.',
              style: AppTypography.bodyMedium.copyWith(color: cs.onSurfaceVariant),
            ),
            const SizedBox(height: AppSpacing.xxl),
            ...AlertSensitivity.values.map((s) {
              final isSelected = s == current;
              return Padding(
                padding: const EdgeInsets.only(bottom: AppSpacing.sm),
                child: _PickerOption(
                  label: s.label,
                  subtitle: _subtitleFor(s),
                  selected: isSelected,
                  onTap: () => onSelected(s),
                ),
              );
            }),
          ],
        ),
      ),
    );
  }

  static String _subtitleFor(AlertSensitivity s) {
    return switch (s) {
      AlertSensitivity.standard => 'Notify at Poor or worse',
      AlertSensitivity.sensitive => 'Notify at Moderately Polluted or worse',
      AlertSensitivity.high => 'Notify early, including forecast warnings',
      AlertSensitivity.custom => 'Set your own thresholds',
    };
  }
}

class _HealthContextPicker extends StatelessWidget {
  const _HealthContextPicker({required this.current, required this.onSelected});

  final UserHealthContext current;
  final ValueChanged<UserHealthContext> onSelected;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;

    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(
            AppSpacing.xl, AppSpacing.xxl, AppSpacing.xl, AppSpacing.xxxxl),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Health Context',
                style: AppTypography.headlineSmall.copyWith(color: cs.onSurface)),
            const SizedBox(height: AppSpacing.md),
            Text(
              'This helps us suggest an appropriate alert sensitivity. '
              'It is NOT a diagnosis and stays on your device only.',
              style: AppTypography.bodyMedium.copyWith(color: cs.onSurfaceVariant),
            ),
            const SizedBox(height: AppSpacing.xxl),
            ...UserHealthContext.values.map((h) {
              return Padding(
                padding: const EdgeInsets.only(bottom: AppSpacing.sm),
                child: _PickerOption(
                  label: h.label,
                  selected: h == current,
                  onTap: () => onSelected(h),
                ),
              );
            }),
          ],
        ),
      ),
    );
  }
}

class _DiseaseSeverityPicker extends StatelessWidget {
  const _DiseaseSeverityPicker({required this.current, required this.onSelected});

  final DiseaseSeverity current;
  final ValueChanged<DiseaseSeverity> onSelected;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;

    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(
            AppSpacing.xl, AppSpacing.xxl, AppSpacing.xl, AppSpacing.xxxxl),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Condition Severity',
                style: AppTypography.headlineSmall.copyWith(color: cs.onSurface)),
            const SizedBox(height: AppSpacing.md),
            Text(
              'Determines your threshold for rising AQI alerts and rapid rise warnings.',
              style: AppTypography.bodyMedium.copyWith(color: cs.onSurfaceVariant),
            ),
            const SizedBox(height: AppSpacing.xxl),
            ...DiseaseSeverity.values.map((sev) {
              return Padding(
                padding: const EdgeInsets.only(bottom: AppSpacing.sm),
                child: _PickerOption(
                  label: sev.label,
                  subtitle: sev.description,
                  selected: sev == current,
                  onTap: () => onSelected(sev),
                ),
              );
            }),
          ],
        ),
      ),
    );
  }
}

class _PickerOption extends StatelessWidget {
  const _PickerOption({
    required this.label,
    required this.selected,
    required this.onTap,
    this.subtitle,
  });

  final String label;
  final String? subtitle;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final borderColor = selected ? cs.primary : cs.outlineVariant;

    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.all(AppSpacing.xl),
        decoration: BoxDecoration(
          color: selected
              ? cs.primary.withValues(alpha: 0.08)
              : cs.surfaceContainerHighest,
          borderRadius: BorderRadius.circular(10),
          border: Border.all(color: borderColor, width: selected ? 1.5 : 0.5),
        ),
        child: Row(
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(label,
                      style: AppTypography.titleMedium.copyWith(color: cs.onSurface)),
                  if (subtitle != null) ...[
                    const SizedBox(height: AppSpacing.xs),
                    Text(subtitle!,
                        style: AppTypography.bodySmall
                            .copyWith(color: cs.onSurfaceVariant)),
                  ],
                ],
              ),
            ),
            if (selected) Icon(Icons.check_circle, color: cs.primary, size: 22),
          ],
        ),
      ),
    );
  }
}

// ── Quiet hours sheet ─────────────────────────────────────────────────

class _QuietHoursSheet extends StatefulWidget {
  const _QuietHoursSheet({required this.initial, required this.onSave});

  final UserAlertPreferences initial;
  final ValueChanged<UserAlertPreferences> onSave;

  @override
  State<_QuietHoursSheet> createState() => _QuietHoursSheetState();
}

class _QuietHoursSheetState extends State<_QuietHoursSheet> {
  late bool _enabled = widget.initial.hasQuietHours;
  late TimeOfDay _start =
      _timeOf(widget.initial.quietHoursStart) ??
          const TimeOfDay(hour: 22, minute: 0);
  late TimeOfDay _end =
      _timeOf(widget.initial.quietHoursEnd) ??
          const TimeOfDay(hour: 7, minute: 0);

  static TimeOfDay? _timeOf(DateTime? dt) =>
      dt == null ? null : TimeOfDay(hour: dt.hour, minute: dt.minute);

  static DateTime _asDateTime(TimeOfDay t) =>
      DateTime(2000, 1, 1, t.hour, t.minute);

  Future<void> _pick(bool isStart) async {
    final picked = await showTimePicker(
      context: context,
      initialTime: isStart ? _start : _end,
    );
    if (picked == null || !mounted) return;
    setState(() {
      if (isStart) {
        _start = picked;
      } else {
        _end = picked;
      }
    });
  }

  void _save() {
    widget.onSave(
      _enabled
          ? widget.initial.copyWith(
              quietHoursStart: _asDateTime(_start),
              quietHoursEnd: _asDateTime(_end),
            )
          : widget.initial.copyWith(clearQuietHours: true),
    );
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(
            AppSpacing.xl, AppSpacing.xxl, AppSpacing.xl, AppSpacing.xxxxl),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Quiet hours',
                style: AppTypography.headlineSmall.copyWith(color: cs.onSurface)),
            const SizedBox(height: AppSpacing.md),
            Text(
              'Silence non-urgent alerts during a window you choose. Urgent '
              'air-quality warnings still come through.',
              style: AppTypography.bodyMedium.copyWith(color: cs.onSurfaceVariant),
            ),
            const SizedBox(height: AppSpacing.lg),
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              title: Text('Enable quiet hours',
                  style: AppTypography.titleMedium.copyWith(color: cs.onSurface)),
              value: _enabled,
              onChanged: (v) => setState(() => _enabled = v),
            ),
            if (_enabled) ...[
              _TimeRow(
                label: 'From',
                time: _start,
                onTap: () => _pick(true),
              ),
              _TimeRow(
                label: 'To',
                time: _end,
                onTap: () => _pick(false),
              ),
            ],
            const SizedBox(height: AppSpacing.xl),
            SizedBox(
              width: double.infinity,
              child: FilledButton(onPressed: _save, child: const Text('Save')),
            ),
          ],
        ),
      ),
    );
  }
}

class _TimeRow extends StatelessWidget {
  const _TimeRow({
    required this.label,
    required this.time,
    required this.onTap,
  });

  final String label;
  final TimeOfDay time;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return ListTile(
      contentPadding: EdgeInsets.zero,
      title: Text(label,
          style: AppTypography.titleMedium.copyWith(color: cs.onSurface)),
      trailing: Text(
        time.format(context),
        style: AppTypography.titleMedium.copyWith(color: cs.primary),
      ),
      onTap: onTap,
    );
  }
}

// ── Custom sensitivity sheet ──────────────────────────────────────────

class _CustomRulesSheet extends StatefulWidget {
  const _CustomRulesSheet({required this.initial, required this.onSave});

  final CustomSensitivityRules initial;
  final ValueChanged<CustomSensitivityRules> onSave;

  @override
  State<_CustomRulesSheet> createState() => _CustomRulesSheetState();
}

class _CustomRulesSheetState extends State<_CustomRulesSheet> {
  late double _warning = widget.initial.warningAqi.toDouble();
  late double _forecast = widget.initial.forecastWarningAqi.toDouble();
  late double _rise = widget.initial.rapidRiseAqiPerHour.toDouble();

  void _save() {
    widget.onSave(CustomSensitivityRules(
      warningAqi: _warning.round(),
      forecastWarningAqi: _forecast.round(),
      rapidRiseAqiPerHour: _rise.round(),
    ));
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(
            AppSpacing.xl, AppSpacing.xxl, AppSpacing.xl, AppSpacing.xxxxl),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Custom sensitivity',
                style: AppTypography.headlineSmall.copyWith(color: cs.onSurface)),
            const SizedBox(height: AppSpacing.md),
            Text(
              'Set your own thresholds. Alerts trigger at or above these AQI '
              'values.',
              style: AppTypography.bodyMedium.copyWith(color: cs.onSurfaceVariant),
            ),
            const SizedBox(height: AppSpacing.lg),
            _SliderRow(
              label: 'Current AQI warning',
              value: _warning,
              min: 50,
              max: 300,
              divisions: 25,
              display: _aqiDisplay(_warning),
              onChanged: (v) => setState(() => _warning = v),
            ),
            _SliderRow(
              label: 'Forecast AQI warning',
              value: _forecast,
              min: 50,
              max: 300,
              divisions: 25,
              display: _aqiDisplay(_forecast),
              onChanged: (v) => setState(() => _forecast = v),
            ),
            _SliderRow(
              label: 'Rapid rise',
              value: _rise,
              min: 10,
              max: 60,
              divisions: 10,
              display: '${_rise.round()} AQI/hour',
              onChanged: (v) => setState(() => _rise = v),
            ),
            const SizedBox(height: AppSpacing.xl),
            SizedBox(
              width: double.infinity,
              child: FilledButton(onPressed: _save, child: const Text('Save')),
            ),
          ],
        ),
      ),
    );
  }

  static String _aqiDisplay(double value) {
    final aqi = value.round();
    return '$aqi (${CpcbCategory.fromAqi(aqi).label})';
  }
}

class _SliderRow extends StatelessWidget {
  const _SliderRow({
    required this.label,
    required this.value,
    required this.min,
    required this.max,
    required this.divisions,
    required this.display,
    required this.onChanged,
  });

  final String label;
  final double value;
  final double min;
  final double max;
  final int divisions;
  final String display;
  final ValueChanged<double> onChanged;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Expanded(
              child: Text(label,
                  style: AppTypography.titleMedium.copyWith(color: cs.onSurface)),
            ),
            Text(display,
                style: AppTypography.bodyMedium.copyWith(color: cs.primary)),
          ],
        ),
        Slider(
          value: value,
          min: min,
          max: max,
          divisions: divisions,
          label: display,
          onChanged: onChanged,
        ),
      ],
    );
  }
}

// ── Lead time picker ──────────────────────────────────────────────────

class _LeadTimePicker extends StatelessWidget {
  const _LeadTimePicker({required this.current, required this.onSelected});

  final Duration current;
  final ValueChanged<Duration> onSelected;

  static const _options = <Duration>[
    Duration(hours: 1),
    Duration(hours: 2),
    Duration(hours: 3),
    Duration(hours: 6),
    Duration(hours: 12),
  ];

  static String _labelFor(Duration d) =>
      d.inHours == 1 ? '1 hour ahead' : '${d.inHours} hours ahead';

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(
            AppSpacing.xl, AppSpacing.xxl, AppSpacing.xl, AppSpacing.xxxxl),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Forecast warning lead time',
                style: AppTypography.headlineSmall.copyWith(color: cs.onSurface)),
            const SizedBox(height: AppSpacing.md),
            Text(
              'How far ahead a forecast alert may look.',
              style: AppTypography.bodyMedium.copyWith(color: cs.onSurfaceVariant),
            ),
            const SizedBox(height: AppSpacing.xxl),
            ..._options.map((d) {
              return Padding(
                padding: const EdgeInsets.only(bottom: AppSpacing.sm),
                child: _PickerOption(
                  label: _labelFor(d),
                  selected: d == current,
                  onTap: () => onSelected(d),
                ),
              );
            }),
          ],
        ),
      ),
    );
  }
}

// ── Alarm lead picker ─────────────────────────────────────────────────

class _AlarmLeadPicker extends StatelessWidget {
  const _AlarmLeadPicker({required this.current, required this.onSelected});

  final Duration current;
  final ValueChanged<Duration> onSelected;

  static const _options = <Duration>[
    Duration.zero,
    Duration(minutes: 10),
    Duration(minutes: 30),
    Duration(hours: 1),
  ];

  static String _labelFor(Duration d) => d == Duration.zero
      ? 'At the predicted time'
      : '${ProfileScreen._alarmLeadOptionLabel(d)} before';

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(
            AppSpacing.xl, AppSpacing.xxl, AppSpacing.xl, AppSpacing.xxxxl),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Alarm lead time',
                style: AppTypography.headlineSmall.copyWith(color: cs.onSurface)),
            const SizedBox(height: AppSpacing.md),
            Text(
              'How early the alarm rings before the forecasted change.',
              style: AppTypography.bodyMedium.copyWith(color: cs.onSurfaceVariant),
            ),
            const SizedBox(height: AppSpacing.xxl),
            ..._options.map((d) {
              return Padding(
                padding: const EdgeInsets.only(bottom: AppSpacing.sm),
                child: _PickerOption(
                  label: _labelFor(d),
                  selected: d == current,
                  onTap: () => onSelected(d),
                ),
              );
            }),
          ],
        ),
      ),
    );
  }
}
