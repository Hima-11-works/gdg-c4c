/// Settings: which backend this console talks to, and the simulator key that
/// lets it write.
library;

import 'package:flutter/material.dart';

import '../api.dart';
import '../config.dart';
import '../fmt.dart';
import '../models.dart';
import '../widgets.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({
    super.key,
    required this.config,
    required this.onSave,
  });

  final SimulatorConfig config;
  final void Function(SimulatorConfig) onSave;

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  late final TextEditingController _baseUrl = TextEditingController(text: widget.config.baseUrl);
  late final TextEditingController _key = TextEditingController(text: widget.config.apiKey);
  late final TextEditingController _actorId = TextEditingController(text: widget.config.actorId);
  late ResponderRole _role = widget.config.role;
  bool _showKey = false;
  bool _testing = false;
  String? _testResult;
  bool _testFailed = false;

  @override
  void dispose() {
    _baseUrl.dispose();
    _key.dispose();
    _actorId.dispose();
    super.dispose();
  }

  Future<void> _testConnection() async {
    setState(() {
      _testing = true;
      _testResult = null;
    });
    // A throwaway client: testing must not depend on what was saved.
    final probe = IncidentApi(
      baseUrl: _baseUrl.text.trim(),
      apiKey: _key.text.trim(),
      timeout: const Duration(seconds: 8),
    );
    try {
      final incidents = await probe.listIncidents();
      if (!mounted) return;
      setState(() {
        _testResult = 'Reached the backend. It returned ${incidents.length} '
            'incident${incidents.length == 1 ? '' : 's'} (reads need no key).';
        _testFailed = false;
      });
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() {
        _testResult = error.isNetwork
            ? error.message
            : 'The backend answered but refused the read: ${error.message}';
        _testFailed = true;
      });
    } finally {
      probe.close();
      if (mounted) setState(() => _testing = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(title: const Text('Settings')),
      body: Column(
        children: [
          const SimulationBanner(dense: true),
          Expanded(
            child: ListView(
              padding: const EdgeInsets.all(16),
              children: [
                Text('Backend', style: theme.textTheme.titleSmall),
                const SizedBox(height: 6),
                TextField(
                  controller: _baseUrl,
                  keyboardType: TextInputType.url,
                  autocorrect: false,
                  decoration: const InputDecoration(
                    labelText: 'Incident API base URL',
                    helperText: 'An Android emulator reaches the host at http://10.0.2.2:8001',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 16),
                Text('Simulator key', style: theme.textTheme.titleSmall),
                const SizedBox(height: 6),
                TextField(
                  controller: _key,
                  obscureText: !_showKey,
                  autocorrect: false,
                  decoration: InputDecoration(
                    labelText: 'X-Simulator-Key',
                    helperText: 'Required for every write. Reads work without it.',
                    border: const OutlineInputBorder(),
                    suffixIcon: IconButton(
                      tooltip: _showKey ? 'Hide' : 'Show',
                      onPressed: () => setState(() => _showKey = !_showKey),
                      icon: Icon(_showKey ? Icons.visibility_off : Icons.visibility),
                    ),
                  ),
                ),
                const SizedBox(height: 16),
                Text('Responder identity', style: theme.textTheme.titleSmall),
                const SizedBox(height: 6),
                TextField(
                  controller: _actorId,
                  autocorrect: false,
                  decoration: const InputDecoration(
                    labelText: 'Registered actor id',
                    helperText: 'Must match an actor in the backend SIMULATOR_ACTORS setting.',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 12),
                DropdownButtonFormField<ResponderRole>(
                  value: _role,
                  decoration: const InputDecoration(labelText: 'Authority role', border: OutlineInputBorder()),
                  items: ResponderRole.values
                      .map((role) => DropdownMenuItem(value: role, child: Text(roleLabel(role))))
                      .toList(),
                  onChanged: (role) {
                    if (role != null) setState(() => _role = role);
                  },
                ),
                const SizedBox(height: 10),
                Text(
                  'The key and actor id are held in memory only. Set SIMULATOR_API_KEY and '
                  'SIMULATOR_ACTOR_ID at launch to prefill them. Writes include the registered '
                  'actor identity; the backend checks its role and jurisdiction.',
                  style: theme.textTheme.bodySmall
                      ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
                ),
                const SizedBox(height: 20),
                Row(
                  children: [
                    FilledButton.icon(
                      onPressed: _testing ? null : _testConnection,
                      icon: _testing
                          ? const SizedBox(
                              width: 16,
                              height: 16,
                              child: CircularProgressIndicator(strokeWidth: 2),
                            )
                          : const Icon(Icons.wifi_tethering, size: 18),
                      label: const Text('Test connection'),
                    ),
                    const SizedBox(width: 12),
                    OutlinedButton(
                      onPressed: () {
                        widget.onSave(SimulatorConfig(
                          baseUrl: _baseUrl.text.trim(),
                          apiKey: _key.text.trim(),
                          role: _role,
                          actorId: _actorId.text.trim(),
                        ));
                        Navigator.of(context).pop();
                      },
                      child: const Text('Save'),
                    ),
                  ],
                ),
                if (_testResult != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 14),
                    child: Text(
                      _testResult!,
                      style: theme.textTheme.bodySmall?.copyWith(
                        color: _testFailed ? theme.colorScheme.error : theme.colorScheme.primary,
                      ),
                    ),
                  ),
                const SizedBox(height: 24),
                Card(
                  child: Padding(
                    padding: const EdgeInsets.all(12),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text('About this console', style: theme.textTheme.titleSmall),
                        const SizedBox(height: 6),
                        Text(
                          'A simulator for the fire-department side of the Air Health incident '
                          'workflow. It reads and writes incidents on the backend and shows new '
                          'inbox assignments as in-app alerts while open. It uses no remote push '
                          'service, contacts no emergency service, and shows only synthetic records. '
                          'Status changes are persistent simulator changes, which is why the API '
                          'requires the key.',
                          style: theme.textTheme.bodySmall,
                        ),
                        const SizedBox(height: 8),
                        Text(
                          'Contract: docs/api/incidents.md. Read-only without a key; writes are '
                          'refused with 401 without one, and with 503 when the server has no key '
                          'configured at all.',
                          style: theme.textTheme.bodySmall
                              ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
                        ),
                      ],
                    ),
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
