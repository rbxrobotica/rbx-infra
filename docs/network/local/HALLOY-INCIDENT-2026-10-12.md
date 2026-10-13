# Halloy black window with a busy main thread

Date: **2026-10-12, America/Sao_Paulo**. Status: failure captured and initial
recovery after a restart with software rendering; **cause and long-term
stability unconfirmed**. The operator supplied a screenshot at approximately
21:51 BRT showing the entire client area black while the window frame remained
visible. The operator confirmed that it remained black during investigation.
They had been using Codex for unrelated work, without suspending the computer;
the screen might have locked after an idle period. Screen locking is a possible
preceding event, not a demonstrated trigger. No scrolling trigger was reported.

## Evidence during failure

The installed client is Halloy **2026.9 (2b3edc8e)** on Linux Mint 22.3,
XFCE/X11, with an RTX 4070 Ti SUPER and NVIDIA driver 595.99.02. PID `914616`
started on October 11 at 20:09:38 BRT and had been running for about 25 hours
45 minutes when sampled. Its lifetime does not establish when the fault began.

The [sanitized evidence](evidence/halloy-black-window-2026-10-12.json) includes
six two-second samples collected between **21:54:45 and 21:55:12 BRT**.
UTC timestamps in the artifact fall on October 13 because BRT is UTC minus 3.

| Measurement | Observed during the black window |
| --- | --- |
| Process CPU, percentage of one core per interval | 101.487% to 102.985% |
| Main-thread CPU in a separate 3-second sample at 22:00 BRT | 99.961%, runnable |
| Resident RAM and process peak RSS | 298,488 KiB, about 291.5 MiB; constant |
| Proportional set size, PSS | 209,200 to 209,201 KiB |
| Process swap | 0 KiB |
| Threads / open descriptors | 69 / 87; constant |
| Halloy TCP connection to loopback port 6667 | Established, send and receive queues empty |
| IRC tunnel and agent service | Active, running, zero restarts since October 8 |

The process can exceed 100% of one core because it has multiple threads.
The separate thread sample localizes almost one full core to the main thread;
it does not provide a call stack or identify which function is busy.

At 22:00 BRT the system had about 16.6 GiB of available RAM. The global NVIDIA
query showed 952 MiB used out of 16,376 MiB, 2% GPU utilization and 37 degrees C.
Those figures cover the whole GPU, not Halloy alone. The observations do not
suggest exhaustion of system RAM or VRAM at capture time.

The configuration hash still matches the
[October 11 baseline](HALLOY-DIAGNOSTIC-2026-10-11.md). The `runtime` table was
absent, the theme remained `ferra`, and no explicit graphics-backend environment
override was found. Graphics libraries were mapped in the process, which does
not by itself establish an NVIDIA driver defect.

Four existing logs contained none of the searched warning, panic, memory or
surface-presentation error indicators. No matching NVRM, Xid or OOM entry was
found in the inspected kernel journal since 20:00 BRT. These searches do not
prove that no graphics failure occurred. A nonprivileged debugger attachment
failed, so **no backtrace was obtained**; tracing restrictions were not changed.

Only persistence metadata was collected during this incident. The 21 history
files totaled 33,390 bytes; their latest modification and the latest log update
were October 11 at 20:46 BRT. Window metadata was updated on October 12 at
21:52 BRT. These timestamps do not establish missing messages or fault onset.
The prior JSON/gzip readability check does not validate logical history state.

## Interpretation and upstream correction

This is a captured display failure with sustained CPU consumption during the
sample. The main-thread activity makes an interface/event-loop problem a
stronger lead than memory exhaustion or a failed SSH tunnel. Stable RSS,
including the process peak, provides no evidence of the multi-gigabyte growth
described in [Halloy #2228](https://github.com/squidowl/halloy/issues/2228).
It does not exclude every memory-management or persistence defect. Established
TCP and healthy services do not prove message delivery or display.

[Halloy #2129](https://github.com/squidowl/halloy/issues/2129) describes idle
panels ceasing to redraw with high CPU and is the closest reported pattern.
[#2437](https://github.com/squidowl/halloy/issues/2437) concerns X11 history
scrolling and one busy core, but that trigger was not reported here.

The earlier diagnostic incorrectly treated
[Halloy PR #2354](https://github.com/squidowl/halloy/pull/2354) as proof that
the NVIDIA recovery fix remained active in this version. Halloy 2026.9 pins
[Iced `3124ac8`](https://github.com/squidowl/halloy/blob/2026.9/Cargo.toml#L169-L171),
whose history includes the
[revert `a4abb0f`](https://github.com/squidowl/iced/commit/a4abb0fe890767dc32604a13cc0a49c2f32819ed).
The [pinned error handler](https://github.com/squidowl/iced/blob/3124ac8a68529d10a34f708431ffc373615f9470/winit/src/lib.rs#L953-L995)
again requests redraw after a generic presentation error.

[Iced PR #3478](https://github.com/iced-rs/iced/pull/3478) describes a cycle of
presentation errors and redraws that consumes CPU without updating the window.
That is a plausible mechanism, not a diagnosis of this Linux/X11 process:
the local logs do **not** contain its reported `Error Other when presenting
surface` signature. Updating to the same Halloy 2026.9 binary is not a new fix.

## Controlled restart with software rendering

A private backup and a candidate configuration were prepared outside the
watched configuration directory, in a `0700` directory with `0600` files.
The operator then authorized the restart. At **22:03 BRT**, the existing window
accepted a normal close request and PID `914616` exited. No termination signal
or forced kill was needed. Only after it stopped was the candidate installed;
the same binary launched as PID `1414652`. The configuration changes only the
officially documented
[runtime backend](https://halloy.chat/configuration/runtime):

```toml
[runtime]
backend = "software"
```

The operator confirmed that the interface returned to normal and supplied a
screenshot showing channels, history and successful authentication at 22:03.
Theme, network, channels and vault integration were preserved, and no persisted
history was deleted. No IRC test messages were sent and no protocol logging was
enabled. This confirms visible recovery and login, not delivery of a new chat
message or durable resolution of the fault.

Six more two-second samples, between **22:03:55 and 22:04:23 BRT**, showed:

| Measurement | After restart with software configured |
| --- | --- |
| Process CPU, percentage of one core per interval | 0.5% to 1.499% |
| Resident RAM, RSS | 118,528 to 118,852 KiB, about 115.8 to 116.1 MiB |
| PSS | 109,790 to 110,114 KiB |
| Swap | 0 KiB |
| Threads / open descriptors | 67 then 64 / 26 |
| IRC TCP connection | Established, send and receive queues empty |

Both the restart record and the post-restart measurements are included in the
sanitized evidence. Configuration semantics were compared before installation:
the backend addition was the only change. The configuration file remains `0600`.

**Initial recovery is verified; the cause is not.** Restarting alone can clear
faulty state, and the prior healthy startup also showed low CPU. The shorter
lifetime and changed backend confound this comparison. Observe ordinary use
over a comparable period, including natural screen lock/unlock and the next
full workday. Software rendering may cost more CPU during redraws; this sample
does not measure an interactive workload. No periodic monitor was installed.

Keep this as a local experiment before changing the Mint bootstrap. Avoid
switching backends through a live reload: the
[2026.9 source](https://github.com/squidowl/halloy/blob/2026.9/src/main.rs#L1665-L1668)
notes a possible NVIDIA/Vulkan crash during that operation. Reversal requires
closing the test process, restoring the private backup and starting again;
preserve any intervening operator configuration changes before restoration.

The private backup and candidate are not repository artifacts. This report and
the JSON evidence contain no configuration contents, credentials, chat payloads
or desktop screenshot. The long-term stability requirement in the
[roadmap](ROADMAP.md#etapa-1-investigar-a-falha-do-halloy) remains open.
