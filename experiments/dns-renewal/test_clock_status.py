"""Mocked read-only clock ABI tests; never calls libc or adjusts a real clock."""

import ctypes
import errno
import pathlib
import sys
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import clock_status


class ReadOnlyClockControls(unittest.TestCase):
    def fake_query(self, maxerror=1000, state=0, status=0):
        def query(argument):
            value = argument._obj
            self.assertIsInstance(value, clock_status.Timex)
            self.assertEqual(value.modes, 0)
            self.assertEqual(ctypes.string_at(ctypes.byref(value), 208), bytes(208))
            value.maxerror = maxerror
            value.status = status
            self.assertEqual(value.modes, 0)
            return state
        function = mock.Mock(side_effect=query)
        return types.SimpleNamespace(adjtimex=function)

    def test_native_linux_64bit_timex_layout(self):
        self.assertEqual(ctypes.sizeof(ctypes.c_long), 8)
        self.assertEqual(ctypes.sizeof(ctypes.c_uint), 4)
        self.assertEqual(ctypes.sizeof(ctypes.c_int), 4)
        self.assertEqual(ctypes.sizeof(clock_status.Timeval), 16)
        self.assertEqual(clock_status.Timeval.tv_sec.offset, 0)
        self.assertEqual(clock_status.Timeval.tv_usec.offset, 8)
        self.assertEqual(ctypes.sizeof(clock_status.Timex), 208)
        self.assertEqual(ctypes.alignment(clock_status.Timex), 8)
        expected = {
            'modes':0, 'offset':8, 'freq':16, 'maxerror':24, 'esterror':32,
            'status':40, 'constant':48, 'precision':56, 'tolerance':64,
            'time':72, 'tick':88, 'ppsfreq':96, 'jitter':104, 'shift':112,
            'stabil':120, 'jitcnt':128, 'calcnt':136, 'errcnt':144,
            'stbcnt':152, 'tai':160, 'padding':164,
        }
        for field, offset in expected.items():
            with self.subTest(field=field):
                self.assertEqual(getattr(clock_status.Timex, field).offset, offset)

    def test_query_is_zero_mode_with_explicit_native_pointer_and_result_types(self):
        libc = self.fake_query(maxerror=1000, state=0, status=0x2000)
        with mock.patch.object(clock_status.ctypes, 'CDLL', return_value=libc) as load:
            result = clock_status.read_clock()
        load.assert_called_once_with(None, use_errno=True)
        libc.adjtimex.assert_called_once()
        self.assertEqual(libc.adjtimex.argtypes, [ctypes.POINTER(clock_status.Timex)])
        self.assertIs(libc.adjtimex.restype, ctypes.c_int)
        self.assertEqual(result, {'source':'kernel_adjtimex_readonly', 'state':0,
                                  'max_error_microseconds':1000,
                                  'kernel_status':0x2000, 'synchronized':True})

    def test_systemd_maxerror_threshold_and_negative_value(self):
        for maxerror, synchronized in [
            (-1, False), (0, True), (15999999, True),
            (16000000, False), (16000001, False),
        ]:
            with self.subTest(maxerror=maxerror):
                libc = self.fake_query(maxerror=maxerror)
                with mock.patch.object(clock_status.ctypes, 'CDLL', return_value=libc):
                    result = clock_status.read_clock()
                self.assertIs(result['synchronized'], synchronized)
                self.assertEqual(result['max_error_microseconds'], maxerror)

    def test_kernel_state_and_status_are_retained_without_replacing_maxerror_policy(self):
        libc = self.fake_query(maxerror=15999999, state=5, status=0x40)
        with mock.patch.object(clock_status.ctypes, 'CDLL', return_value=libc):
            result = clock_status.read_clock()
        self.assertEqual(result['state'], 5)
        self.assertEqual(result['kernel_status'], 0x40)
        self.assertTrue(result['synchronized'])

    def test_negative_kernel_result_raises_and_never_reports_success(self):
        libc = self.fake_query(state=-1)
        with mock.patch.object(clock_status.ctypes, 'CDLL', return_value=libc), \
                mock.patch.object(clock_status.ctypes, 'get_errno', return_value=errno.EPERM):
            with self.assertRaises(OSError) as caught:
                clock_status.read_clock()
        self.assertEqual(caught.exception.errno, errno.EPERM)
        self.assertIn('kernel_clock_query_failed', str(caught.exception))

    def test_loader_failure_propagates_without_healthy_fallback(self):
        with mock.patch.object(clock_status.ctypes, 'CDLL',
                               side_effect=OSError(errno.ENOENT, 'mock unavailable libc')):
            with self.assertRaises(OSError):
                clock_status.read_clock()

    def test_nonlinux_platform_fails_before_loading_libc(self):
        with mock.patch.object(clock_status.sys, 'platform', 'not-linux'), \
                mock.patch.object(clock_status.ctypes, 'CDLL') as load:
            with self.assertRaisesRegex(ValueError, 'unsupported_readonly_clock_abi'):
                clock_status.read_clock()
        load.assert_not_called()

    def test_unsupported_long_or_timex_width_fails_before_loading_libc(self):
        native_sizeof = ctypes.sizeof
        for unsupported in ['long', 'timex']:
            with self.subTest(unsupported=unsupported):
                def wrong_sizeof(value):
                    if unsupported == 'long' and value is ctypes.c_long:
                        return 4
                    if unsupported == 'timex' and value is clock_status.Timex:
                        return 216
                    return native_sizeof(value)
                with mock.patch.object(clock_status.ctypes, 'sizeof', side_effect=wrong_sizeof), \
                        mock.patch.object(clock_status.ctypes, 'CDLL') as load:
                    with self.assertRaisesRegex(ValueError, 'unsupported_readonly_clock_abi'):
                        clock_status.read_clock()
                load.assert_not_called()


if __name__ == '__main__':
    unittest.main()
