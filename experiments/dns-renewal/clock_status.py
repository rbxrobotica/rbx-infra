"""Read-only Linux clock synchronization query; never adjusts the clock.

This uses the same kernel maxerror criterion as systemd timedated, without a
system-bus dependency. modes=0 does not require CAP_SYS_TIME. The explicit
native ABI is Linux 64-bit, matching the declared workstation/runtime.
"""
import ctypes
import sys


class Timeval(ctypes.Structure):
    _fields_=[('tv_sec',ctypes.c_long),('tv_usec',ctypes.c_long)]


class Timex(ctypes.Structure):
    _fields_=[('modes',ctypes.c_uint),('offset',ctypes.c_long),('freq',ctypes.c_long),
              ('maxerror',ctypes.c_long),('esterror',ctypes.c_long),('status',ctypes.c_int),
              ('constant',ctypes.c_long),('precision',ctypes.c_long),('tolerance',ctypes.c_long),
              ('time',Timeval),('tick',ctypes.c_long),('ppsfreq',ctypes.c_long),('jitter',ctypes.c_long),
              ('shift',ctypes.c_int),('stabil',ctypes.c_long),('jitcnt',ctypes.c_long),
              ('calcnt',ctypes.c_long),('errcnt',ctypes.c_long),('stbcnt',ctypes.c_long),
              ('tai',ctypes.c_int),('padding',ctypes.c_int*11)]


def read_clock():
    if sys.platform!='linux' or ctypes.sizeof(ctypes.c_long)!=8 or ctypes.sizeof(Timex)!=208:
        raise ValueError('unsupported_readonly_clock_abi')
    value=Timex()  # Zero initialization: modes=0 is strictly read-only.
    call=ctypes.CDLL(None,use_errno=True).adjtimex
    call.argtypes=[ctypes.POINTER(Timex)];call.restype=ctypes.c_int
    result=call(ctypes.byref(value))
    if result<0:raise OSError(ctypes.get_errno(),'kernel_clock_query_failed')
    return {'source':'kernel_adjtimex_readonly','state':result,
            'max_error_microseconds':value.maxerror,'kernel_status':value.status,
            'synchronized':0<=value.maxerror<16000000}
