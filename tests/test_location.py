import contextlib
import io
import math
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch, Mock
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mac_location_lab as lab


class Validation(unittest.TestCase):
    def test_valid_boundaries(self):
        self.assertEqual(lab.validate_location(-90, 180, 1), (-90, 180, 1))

    def test_invalid_inputs(self):
        for values in [(91, 0, 10), (0, -181, 10), (0, 0, 0), (0, 0, 100001),
                       (math.nan, 0, 10), (0, math.inf, 10)]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                lab.validate_location(*values)

    def test_missing_preference_is_not_permission_failure(self):
        missing = subprocess.CompletedProcess([], 1, '', 'The domain/default pair does not exist')
        denied = subprocess.CompletedProcess([], 1, '', 'Permission denied')
        for result, raises in [(missing, False), (denied, True)]:
            with patch.object(lab, 'run', side_effect=[Mock(stdout='205'), result]):
                if raises:
                    with self.assertRaises(RuntimeError):
                        lab.preference(lab.DOMAINS[0], 'read', lab.KEYS[0])
                else:
                    self.assertIsNone(lab.preference(lab.DOMAINS[0], 'read', lab.KEYS[0]))

    def test_restoration_preserves_absence(self):
        with patch.object(lab, 'preference') as pref:
            lab.restore({'domain': {'missing': None, 'existing': 12.5}})
            self.assertEqual(pref.call_args_list[0].args, ('domain', 'delete', 'missing', None))
            self.assertEqual(pref.call_args_list[1].args, ('domain', 'write', 'existing', 12.5))


class Commands(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.state = Path(directory)
        for target, value in [('STATE', self.state)]:
            self.stack.enter_context(patch.object(lab, target, value))
        self.stack.enter_context(patch.object(lab.platform, 'system', return_value='Darwin'))
        self.stack.enter_context(patch.object(lab.os, 'geteuid', return_value=0))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.stack.enter_context(patch.object(lab, 'private_directory'))
        self.original = {d: {k: None for k in lab.KEYS} for d in lab.DOMAINS}
        self.stack.enter_context(patch.object(lab, 'snapshot', return_value=self.original))
        self.save = self.stack.enter_context(patch.object(lab, 'save_backup'))
        self.read = self.stack.enter_context(patch.object(lab, 'read_backup', return_value=self.original))
        self.pref = self.stack.enter_context(patch.object(lab, 'preference'))
        self.restart = self.stack.enter_context(patch.object(lab, 'restart_location'))
        self.restore = self.stack.enter_context(patch.object(lab, 'restore'))
        self.refresh = self.stack.enter_context(patch.object(lab, 'refresh_findmy'))

    def command(self, *extra):
        lab.main(['set', '--latitude', '48.8584', '--longitude', '2.2945', *extra])

    def test_set_backs_up_before_six_writes(self):
        self.command()
        self.save.assert_called_once_with(self.original)
        self.assertEqual(self.pref.call_count, 6)
        self.restart.assert_called_once()
        self.refresh.assert_not_called()

    def test_repeated_set_preserves_original_backup(self):
        (self.state / 'original.json').touch()
        self.command()
        self.save.assert_not_called()
        self.read.assert_called_once()

    def test_partial_write_rolls_back(self):
        self.pref.side_effect = [None, RuntimeError('write failed')]
        with self.assertRaises(RuntimeError):
            self.command()
        self.restore.assert_called_once_with(self.original)
        self.restart.assert_called_once()

    def test_restart_failure_rolls_back(self):
        self.restart.side_effect = [RuntimeError('restart failed'), None]
        with self.assertRaises(RuntimeError):
            self.command()
        self.restore.assert_called_once_with(self.original)

    def test_reset_restores_then_removes_backup(self):
        backup = self.state / 'original.json'
        backup.touch()
        lab.main(['reset'])
        self.restore.assert_called_once_with(self.original)
        self.assertFalse(backup.exists())

    def test_failed_reset_retains_backup(self):
        backup = self.state / 'original.json'
        backup.touch()
        self.restart.side_effect = RuntimeError('restart failed')
        with self.assertRaises(RuntimeError):
            lab.main(['reset'])
        self.assertTrue(backup.exists())

    def test_no_session_reset_is_noop(self):
        lab.main(['reset'])
        self.pref.assert_not_called()
        self.restart.assert_not_called()

    def test_findmy_requires_explicit_option(self):
        self.command('--refresh-findmy')
        self.refresh.assert_called_once()


if __name__ == '__main__':
    unittest.main()
