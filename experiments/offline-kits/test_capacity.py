import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('capacity', Path(__file__).with_name('capacity.py'))
capacity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capacity)


class CapacityTests(unittest.TestCase):
    def report(self, ok=2, lost=0, corrupt=0, changed=0, overwritten=0):
        return (f'Data OK: 1.00 KB ({ok} sectors)\n'
                f'Data LOST: 0.00 Byte ({lost} sectors)\n'
                f'Corrupted: 0.00 Byte ({corrupt} sectors)\n'
                f'Slightly changed: 0.00 Byte ({changed} sectors)\n'
                f'Overwritten: 0.00 Byte ({overwritten} sectors)\n')

    def test_exact_sector_reconciliation(self):
        self.assertEqual(capacity.parse_read(self.report(), 1024)['Data OK'], 2)

    def test_bad_data_is_rejected_even_if_process_exit_was_zero(self):
        for kwargs in ({'lost': 1}, {'corrupt': 1}, {'changed': 1}, {'overwritten': 1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                capacity.parse_read(self.report(**kwargs), 1024)

    def test_truncated_missing_or_duplicated_summary_refused(self):
        for text in (self.report().split('Overwritten:')[0], self.report()+self.report(), ''):
            with self.subTest(text=text), self.assertRaises(ValueError):
                capacity.parse_read(text, 1024)

    def test_partial_read_cannot_qualify_full_written_size(self):
        with self.assertRaises(ValueError):
            capacity.parse_read(self.report(ok=1), 1024)

    def test_unexpected_personal_file_stops_cleanup_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'1.h2w').write_bytes(b'x'*512)
            (root/'personal.txt').write_text('preserve')
            fd = os.open(root, os.O_DIRECTORY)
            try:
                with self.assertRaises(ValueError):
                    capacity.files(fd)
                self.assertEqual((root/'personal.txt').read_text(), 'preserve')
            finally:
                os.close(fd)

    def test_symlink_cannot_enter_cleanup_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'target').write_text('preserve')
            (root/'1.h2w').symlink_to(root/'target')
            fd = os.open(root, os.O_DIRECTORY)
            try:
                with self.assertRaises(ValueError):
                    capacity.files(fd)
            finally:
                os.close(fd)

    def test_nonempty_media_refused_without_touching_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); media = root/'media'; state = root/'state'
            media.mkdir(); state.mkdir(mode=0o700)
            (media/'personal.txt').write_bytes(b'original')
            with patch.object(capacity, 'BASE', state), patch.object(capacity, 'device',
                      return_value=(media, media.stat().st_dev)):
                with self.assertRaisesRegex(ValueError, 'require_empty_pinned_usb_root'):
                    capacity.run('A', True)
            self.assertEqual(sorted(p.name for p in media.iterdir()), ['personal.txt'])
            self.assertEqual((media/'personal.txt').read_bytes(), b'original')

    def test_open_directory_stays_pinned_after_path_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); original = root/'mount'; original.mkdir()
            (original/'1.h2w').write_bytes(b'x'*512)
            fd = os.open(original, os.O_DIRECTORY)
            try:
                original.rename(root/'old-mount'); original.mkdir()
                (original/'personal.txt').write_text('other filesystem')
                self.assertEqual(set(capacity.files(fd)), {'1.h2w'})
                self.assertEqual((original/'personal.txt').read_text(), 'other filesystem')
            finally:
                os.close(fd)

    def test_cleanup_preserves_replacement_captured_after_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); test = root/'test'; quarantine = root/'quarantine'
            test.mkdir(); quarantine.mkdir()
            file = test/'1.h2w'; file.write_bytes(b'x'*512)
            first = os.open(test, os.O_DIRECTORY); second = os.open(quarantine, os.O_DIRECTORY)
            try:
                expected = capacity.files(first)
                file.unlink(); file.write_bytes(b'user replacement')
                with self.assertRaisesRegex(ValueError, 'preserved_in_quarantine'):
                    capacity.clean_owned(first, second, expected)
                entries = list(quarantine.iterdir())
                self.assertEqual(len(entries), 1)
                self.assertEqual(entries[0].read_bytes(), b'user replacement')
            finally:
                os.close(first); os.close(second)

    def test_verified_own_files_can_be_removed_without_touching_neighbor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); test = root/'test'; quarantine = root/'quarantine'
            test.mkdir(); quarantine.mkdir(); (root/'personal').write_bytes(b'keep')
            (test/'1.h2w').write_bytes(b'x'*512)
            first = os.open(test, os.O_DIRECTORY); second = os.open(quarantine, os.O_DIRECTORY)
            try:
                capacity.clean_owned(first, second, capacity.files(first))
                self.assertFalse(list(test.iterdir())); self.assertFalse(list(quarantine.iterdir()))
                self.assertEqual((root/'personal').read_bytes(), b'keep')
            finally:
                os.close(first); os.close(second)


if __name__ == '__main__':
    unittest.main()
