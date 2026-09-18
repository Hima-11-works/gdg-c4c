import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../domain/models/models.dart';
import '../../providers/prefs_providers.dart';
import '../../providers/profile_providers.dart';
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
          return ListView(
            padding: const EdgeInsets.only(bottom: AppSpacing.xxxxl),
            children: [
              // ── Location ──────────────────────────────────────────
              _SectionHeader('Location'),
              _SettingsTile(
                icon: Icons.location_on_outlined,
                title: 'Current location',
                subtitle: 'Bhubaneswar',
                onTap: () {},
              ),
              _SettingsTile(
                icon: Icons.location_searching,
                title: 'Location permission',
                subtitle: 'Approximate location — used for nearby data',
                onTap: () {},
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
                subtitle: 'Manage system notification access',
                onTap: () {},
              ),
              _TimeRangeTile(
                icon: Icons.do_not_disturb_on_outlined,
                title: 'Quiet hours',
                subtitle: 'No notifications during this time',
                startHour: null,
                endHour: null,
                onTap: () => _pickQuietHours(context),
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
                subtitle: 'How far ahead to warn',
                onTap: () {},
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
                subtitle: 'Air Health v1.0.0',
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
        },
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

  // ── Quiet hours picker (stub) ────────────────────────────────────────

  void _pickQuietHours(BuildContext context) {
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('Quiet hours picker — coming soon')),
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
        title: const Text('About Air Health'),
        content: const Text(
          'Personal air-quality awareness app.\n\n'
          'Air Health shows local pollution levels, short-term '
          'forecasts, and personalised alerts — so you can make '
          'informed decisions about outdoor exposure.\n\n'
          'This is an environmental awareness tool, not a '
          'medical device.',
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
