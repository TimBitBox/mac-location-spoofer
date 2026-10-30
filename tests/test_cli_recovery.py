"""CLI recovery tests using temporary files and a simulated macOS boundary."""
import contextlib
import io
import os
import plistlib
import subprocess
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mac_location_lab as lab


class SimulatedMac:
    def __init__(self):
        self.preferences = {}
        self.locationd_pid = 100
        self.findmy_open_status = 0
        self.process_query_status = 0
        self.console_uid = '501'
        self.console_uid_after_restart = '501'
        self.missing_preference_message = 'The domain/default pair does not exist'
        self.restart_status = 0
        self.delete_error = None
        self.allow_service_control = True
        self.notification_failures = 0
        self.allow_notifications = True
        self.discard_writes = False
        self.discard_deletes = False
        self.discarded_write_keys = set()
        self.float32_storage = False
        self.human_float_precision = None
        self.export_error = None
        self.export_override = None

    def run(self, args, check=True, **kwargs):
        if not self.allow_notifications and args[0] == '/usr/bin/notifyutil':
            raise AssertionError('Notifications are forbidden in this scenario')
        if not self.allow_service_control and (
                args[0] == '/usr/bin/killall' or args[:2] == ['/bin/launchctl', 'kill']):
            raise AssertionError('Service control is forbidden in this scenario')
        status, stdout, stderr = 0, '', ''
        if args == ['/usr/bin/id', '-u', '_locationd']:
            stdout = '205\n'
        elif args == ['/usr/bin/stat', '-f', '%u', '/dev/console']:
            stdout = self.console_uid + '\n'
        elif args == ['/usr/bin/csrutil', 'status']:
            stdout = 'System Integrity Protection status: enabled.\n'
        elif args[:9] == ['/bin/launchctl', 'asuser', '205', '/usr/bin/sudo',
                         '-H', '-u', '_locationd', '/usr/bin/defaults', '-currentHost']:
            operation, domain, key = args[9:12]
            entry = (domain, key)
            if operation == 'read':
                if entry in self.preferences:
                    value = self.preferences[entry]
                    stdout = (format(value, f'.{self.human_float_precision}g')
                              if self.human_float_precision else str(value))
                else:
                    status = 1
                    stderr = self.missing_preference_message.format(key=key, domain=domain)
            elif operation == 'export':
                if self.export_error is not None:
                    status, stderr = 1, self.export_error
                elif self.export_override is not None:
                    stdout = self.export_override
                else:
                    stdout = plistlib.dumps({key: value for (stored_domain, key), value
                                            in self.preferences.items() if stored_domain == domain}).decode()
            elif operation == 'write':
                if not self.discard_writes and entry not in self.discarded_write_keys:
                    value = float(args[13])
                    if self.float32_storage:
                        value = struct.unpack('f', struct.pack('f', value))[0]
                    self.preferences[entry] = value
            elif operation == 'delete':
                if self.discard_deletes:
                    pass
                elif self.delete_error is not None:
                    status, stderr = 1, self.delete_error
                elif entry in self.preferences:
                    self.preferences.pop(entry)
                else:
                    status = 1
                    stderr = self.missing_preference_message.format(key=key, domain=domain)
            else:
                raise AssertionError('Unexpected preference operation: ' + operation)
        elif args == ['/usr/bin/pgrep', '-x', 'locationd']:
            status = self.process_query_status
            stdout = str(self.locationd_pid) + '\n' if status == 0 else ''
            stderr = 'simulated process query failure' if status > 1 else ''
        elif args == ['/bin/launchctl', 'kill', 'SIGTERM', 'system/com.apple.locationd']:
            status = self.restart_status
            if status:
                stderr = 'Operation not permitted'
            else:
                self.locationd_pid += 1
                self.console_uid = self.console_uid_after_restart
        elif args in (['/usr/bin/killall', '-u', 'root', 'cfprefsd'],
                      ['/usr/bin/notifyutil', '-p', 'com.apple.locationd/Prefs'],
                      ['/bin/launchctl', 'kill', 'SIGTERM', 'system/com.apple.icloud.findmydeviced']):
            if args[0] == '/usr/bin/notifyutil' and self.notification_failures:
                self.notification_failures -= 1
                status, stderr = 1, 'Simulated notification failure'
        elif args == ['/bin/launchctl', 'asuser', '501', '/usr/bin/sudo', '-H', '-u',
                      '#501', '/usr/bin/open', '-g', '-b', 'com.apple.findmy']:
            status = self.findmy_open_status
        else:
            raise AssertionError('Unexpected system command: ' + repr(args))
        result = subprocess.CompletedProcess(args, status, stdout, stderr)
        if check:
            result.check_returncode()
        return result


class CliRecovery(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        folder = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.state = Path(folder) / 'state'
        self.system = SimulatedMac()
        self.stack.enter_context(patch.object(lab, 'STATE', self.state))
        self.stack.enter_context(patch.object(lab.platform, 'system', return_value='Darwin'))
        self.stack.enter_context(patch.object(lab.os, 'geteuid', return_value=0))
        self.stack.enter_context(patch.dict(lab.os.environ, {'SUDO_UID': '501'}))
        self.stack.enter_context(patch.object(lab.subprocess, 'run', side_effect=self.system.run))
        self.stack.enter_context(patch.object(lab.time, 'sleep'))
        real_lstat, real_fstat = os.lstat, os.fstat

        def root_owned(info):
            fields = list(info)
            fields[4] = 0
            return os.stat_result(fields)

        def state_lstat(path, *args, **kwargs):
            info = real_lstat(path, *args, **kwargs)
            return root_owned(info) if Path(path) == self.state else info

        self.stack.enter_context(patch.object(lab.os, 'lstat', side_effect=state_lstat))
        self.stack.enter_context(patch.object(lab.os, 'fstat',
                                            side_effect=lambda fd: root_owned(real_fstat(fd))))

    def command(self, *args):
        self.stdout, self.stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(self.stdout), contextlib.redirect_stderr(self.stderr):
            lab.main(list(args))
        return self.stdout.getvalue()

    def set_location(self, *extra):
        return self.command('set', '--latitude', '48.8584', '--longitude', '2.2945', *extra)

    def test_invalid_findmy_user_does_not_start_a_location_session(self):
        with patch.dict(lab.os.environ, {'SUDO_UID': '0'}):
            with self.assertRaisesRegex(RuntimeError, 'logged-in desktop user'):
                self.set_location('--refresh-findmy')
        self.assertIn('No saved session to reset', self.command('reset'))

    def test_findmy_refresh_failure_keeps_a_resettable_successful_session(self):
        self.system.findmy_open_status = 1
        self.assertIn('Simulation preferences applied', self.set_location('--refresh-findmy'))
        self.assertIn('Find My refresh failed; simulation remains active', self.stderr.getvalue())
        self.assertIn('Original simulation preferences restored', self.command('reset'))

    def test_console_user_change_after_set_skips_findmy_with_a_warning(self):
        self.system.console_uid_after_restart = '502'
        self.assertIn('Simulation preferences applied', self.set_location('--refresh-findmy'))
        self.assertIn('Find My refresh failed; simulation remains active', self.stderr.getvalue())

    def test_interrupted_backup_allows_a_new_session_and_reset(self):
        real_fdopen = os.fdopen

        class InterruptedWriter:
            def __init__(self, stream):
                self.stream = stream

            def __getattr__(self, name):
                return getattr(self.stream, name)

            def __enter__(self):
                self.stream.__enter__()
                return self

            def __exit__(self, *args):
                return self.stream.__exit__(*args)

            def write(self, text):
                self.stream.write(text[:1])
                raise OSError('simulated disk write failure')

        def interrupted_output(fd, mode='r', *args, **kwargs):
            stream = real_fdopen(fd, mode, *args, **kwargs)
            return InterruptedWriter(stream) if mode == 'w' else stream

        with patch.object(lab.os, 'fdopen', side_effect=interrupted_output):
            with self.assertRaisesRegex(OSError, 'simulated disk write failure'):
                self.set_location()
        self.assertIn('Simulation preferences applied', self.set_location())
        self.assertIn('Original simulation preferences restored', self.command('reset'))

    def test_doctor_reports_unknown_when_process_query_fails(self):
        self.system.process_query_status = 3
        self.assertIn('Core Location running: unknown (process query failed)',
                      self.command('doctor'))

    def test_missing_keys_on_macos_27_allow_setting_and_resetting(self):
        self.system.missing_preference_message = (
            "Error: Could not find key '{key}' in domain '{domain}'.")
        self.assertIn('Simulation preferences applied', self.set_location())
        # Model the system clearing the keys before the user requests reset.
        self.system.preferences.clear()
        self.assertIn('Original simulation preferences restored', self.command('reset'))
        self.assertIn('No saved session to reset', self.command('reset'))

    def test_other_preference_errors_identify_the_failed_read_and_leave_no_session(self):
        for message in [
                'Permission denied',
                "Error: Could not find key 'OtherKey' in domain 'com.apple.locationd'.",
                "Error: Could not find key '{key}' in domain 'another.domain'.",
                "Error: Could not find key '{key}' in domain '{domain}'.\nPermission denied"]:
            with self.subTest(message=message):
                self.system.missing_preference_message = message
                with self.assertRaisesRegex(
                        RuntimeError,
                        r'preference read failed for com\.apple\.locationd/SimulatedLocationLatitude'):
                    self.set_location()
                self.assertIn('No saved session to reset', self.command('reset'))

    def test_service_restart_failure_reports_stage_and_keeps_session_resettable(self):
        self.system.restart_status = 1
        with self.assertRaisesRegex(
                RuntimeError, r'Requesting Core Location restart failed \(exit 1; operation not permitted\)'):
            self.set_location()
        self.system.restart_status = 0
        self.assertIn('Original simulation preferences restored', self.command('reset'))

    def test_recovery_distinguishes_restored_preferences_from_failed_service_refresh(self):
        self.system.restart_status = 1
        with self.assertRaises(RuntimeError):
            self.set_location()
        self.assertIn('Pre-command preferences restored, but Core Location refresh failed',
                      self.stderr.getvalue())
        self.assertNotIn('Automatic rollback failed', self.stderr.getvalue())

    def test_reset_reports_restored_preferences_and_preserves_backup_if_refresh_fails(self):
        self.set_location()
        self.system.restart_status = 1
        with self.assertRaisesRegex(RuntimeError, 'Requesting Core Location restart failed'):
            self.command('reset')
        self.assertIn('Original simulation preferences restored, but Core Location refresh failed',
                      self.stderr.getvalue())
        self.system.restart_status = 0
        self.assertIn('Original simulation preferences restored', self.command('reset'))

    def test_reset_accepts_an_absent_key_after_an_unrecognized_delete_error(self):
        self.set_location()
        self.system.preferences.clear()
        self.system.delete_error = 'Simulated delete failure with an unrecognized message'
        self.assertIn('Original simulation preferences restored', self.command('reset'))
        self.assertIn('No saved session to reset', self.command('reset'))

    def test_reset_reports_denied_deletion_of_present_keys_and_retains_backup(self):
        self.set_location()
        self.system.delete_error = 'Operation not permitted'
        with self.assertRaisesRegex(
                RuntimeError,
                r'delete failed for com\.apple\.locationd/SimulatedLocationLatitude '
                r'\(exit 1; operation not permitted\)'):
            self.command('reset')
        self.system.delete_error = None
        self.assertIn('Original simulation preferences restored', self.command('reset'))

    def test_notify_only_can_set_and_reset_when_service_control_is_forbidden(self):
        self.system.allow_service_control = False
        self.assertIn('Simulation preferences saved; preference notification posted',
                      self.set_location('--notify-only'))
        self.assertIn('Original simulation preferences restored; preference notification posted',
                      self.command('reset', '--notify-only'))
        self.assertIn('No saved session to reset', self.command('reset', '--notify-only'))

    def test_notify_only_rejects_findmy_restart_before_starting_a_session(self):
        self.system.allow_service_control = False
        with self.assertRaisesRegex(ValueError, '--notify-only cannot be combined with --refresh-findmy'):
            self.set_location('--notify-only', '--refresh-findmy')
        self.assertIn('No saved session to reset', self.command('reset', '--notify-only'))

    def test_notify_only_notification_failure_can_recover_without_service_control(self):
        self.system.allow_service_control = False
        self.system.notification_failures = 1
        with self.assertRaisesRegex(RuntimeError, 'Posting Core Location preferences notification failed'):
            self.set_location('--notify-only')
        self.assertNotIn('rollback failed', self.stderr.getvalue())
        self.assertIn('Original simulation preferences restored; preference notification posted',
                      self.command('reset', '--notify-only'))

    def test_deferred_set_and_reset_leave_activation_for_a_manual_reboot(self):
        self.system.allow_service_control = False
        self.system.allow_notifications = False
        self.assertIn('activation pending a manual restart of macOS',
                      self.set_location('--defer-refresh'))
        self.assertIn('Original stored preferences restored',
                      self.command('reset', '--defer-refresh'))
        self.assertIn('No saved session to reset', self.command('reset', '--defer-refresh'))

    def test_deferred_set_detects_ignored_writes_and_keeps_recovery_available(self):
        self.system.allow_service_control = False
        self.system.allow_notifications = False
        self.system.discard_writes = True
        with self.assertRaisesRegex(RuntimeError, 'Preference read-back verification failed'):
            self.set_location('--defer-refresh')
        self.system.discard_writes = False
        self.assertIn('Original stored preferences restored',
                      self.command('reset', '--defer-refresh'))

    def test_deferred_reset_verifies_absence_before_discarding_the_backup(self):
        self.system.allow_service_control = False
        self.system.allow_notifications = False
        self.set_location('--defer-refresh')
        self.system.discard_deletes = True
        with self.assertRaisesRegex(RuntimeError, 'Preference read-back verification failed'):
            self.command('reset', '--defer-refresh')
        self.system.discard_deletes = False
        self.assertIn('Original stored preferences restored',
                      self.command('reset', '--defer-refresh'))

    def test_deferred_rollback_reports_a_failed_restoration_without_touching_services(self):
        self.system.allow_service_control = False
        self.system.allow_notifications = False
        self.system.discarded_write_keys.add((lab.DOMAINS[-1], lab.KEYS[-1]))
        self.system.discard_deletes = True
        with self.assertRaisesRegex(RuntimeError, 'Preference read-back verification failed'):
            self.set_location('--defer-refresh')
        self.assertIn('Automatic rollback failed while restoring preferences; run reset --defer-refresh',
                      self.stderr.getvalue())
        self.system.discard_deletes = False
        self.assertIn('Original stored preferences restored',
                      self.command('reset', '--defer-refresh'))

    def test_deferred_set_rejects_findmy_refresh_before_changing_preferences(self):
        self.system.allow_service_control = False
        self.system.allow_notifications = False
        with self.assertRaisesRegex(ValueError, '--defer-refresh cannot be combined with --refresh-findmy'):
            self.set_location('--defer-refresh', '--refresh-findmy')
        self.assertIn('No saved session to reset', self.command('reset', '--defer-refresh'))

    def test_deferred_round_trip_accepts_rounding_and_verifies_existing_original_values(self):
        self.system.allow_service_control = False
        self.system.allow_notifications = False
        self.system.float32_storage = True
        for domain in lab.DOMAINS:
            self.system.preferences.update({
                (domain, 'SimulatedLocationLatitude'): 12.5,
                (domain, 'SimulatedLocationLongitude'): -42.25,
                (domain, 'SimulatedLocationAccuracy'): 8.0,
            })
        self.assertIn('saved and read back', self.set_location('--defer-refresh'))
        self.system.discard_writes = True
        with self.assertRaisesRegex(RuntimeError, 'Preference read-back verification failed'):
            self.command('reset', '--defer-refresh')
        self.system.discard_writes = False
        self.assertIn('Original stored preferences restored and read back',
                      self.command('reset', '--defer-refresh'))

    def test_deferred_and_notification_refresh_modes_are_mutually_exclusive(self):
        with self.assertRaises(SystemExit) as stopped:
            self.set_location('--defer-refresh', '--notify-only')
        self.assertEqual(stopped.exception.code, 2)
        self.assertIn('No saved session to reset', self.command('reset', '--defer-refresh'))

    def test_caltech_readback_uses_full_precision_instead_of_rounded_display_text(self):
        self.system.allow_service_control = False
        self.system.allow_notifications = False
        self.system.float32_storage = True
        self.system.human_float_precision = 7
        output = self.command('set', '--latitude', '34.13556', '--longitude', '-118.12627',
                              '--accuracy', '10', '--defer-refresh')
        self.assertIn('Preferences saved and read back', output)
        self.assertIn('Original stored preferences restored and read back',
                      self.command('reset', '--defer-refresh'))

    def test_precise_read_failure_does_not_fall_back_to_display_text(self):
        self.system.allow_service_control = False
        self.system.allow_notifications = False
        self.system.export_error = 'Permission denied'
        with self.assertRaisesRegex(RuntimeError, 'Reading full-precision preference .* failed'):
            self.set_location('--defer-refresh')
        self.system.export_error = None
        self.assertIn('Original stored preferences restored',
                      self.command('reset', '--defer-refresh'))

    def test_malformed_precise_read_keeps_recovery_and_does_not_print_export_data(self):
        self.system.allow_service_control = False
        self.system.allow_notifications = False
        self.system.export_override = 'invalid property list: PRIVATE-VALUE'
        with self.assertRaisesRegex(RuntimeError, 'not a valid property list') as stopped:
            self.set_location('--defer-refresh')
        self.assertNotIn('PRIVATE-VALUE', str(stopped.exception))
        self.system.export_override = None
        self.assertIn('Original stored preferences restored',
                      self.command('reset', '--defer-refresh'))
