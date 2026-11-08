# Synthetic GPG emergency kit fixture

Prepared: 2026-11-08. This increment qualifies a compatibility mechanism using
temporary generated material only. **Production GPG export, independent custody
and quorum release remain NOT IMPLEMENTED.** No real export, media preparation,
cloud upload, custodian delivery or recovery result is established by this guide.

The owner confirmed that a real GPG recovery copy still needs preparation and
checking. A successful synthetic fixture cannot establish that this copy exists
or opens. The [first real backup contract](HEADSCALE-FIRST-REAL-BACKUP.md) and
the merged [founder-continuity requirements in ADR 0007](https://github.com/rbxrobotica/rbx-resilience/blob/a317ecaa4a0da9f3b8340e059f56b93bc9695bca/docs/adr/0007-founder-independent-gpg-recovery-custody.md)
remain operative.

## Scope and execution

[`gpg_emergency_fixture.py`](../../scripts/resilience/gpg_emergency_fixture.py)
accepts no arguments: no real key, home, password-store path,
passphrase, media or provider can be supplied. It generates all material inside
disposable temporary storage. Use the repository's enforced sandbox:

```bash
bash scripts/resilience/run-local-fixture.sh
```

The wrapper requires rootless local Podman and a cached, digest-pinned runtime.
It disables networking, uses an unprivileged user, a read-only root filesystem,
dropped capabilities, finite resources and disposable tmpfs. Only fixture code
and tests are mounted read-only. It forwards no host home, GPG/SSH agent,
credential store, real key, removable medium or cloud credential, and performs
no implicit image pull.

Direct development execution does not enforce those isolation boundaries:

```bash
python3 -B scripts/resilience/gpg_emergency_fixture.py
```

Never point a modified fixture at real material or describe this direct command
as a network-isolated recovery drill. The fixture is not an executable recipe
for exporting the owner's key, formatting media or distributing release shares.

## Mechanism under test

1. Generate a temporary protected GPG primary key and encryption subkey. Encrypt
   a non-sensitive canary entry using that generated recipient. This models the
   decryption dependency of an encrypted credential-store entry, without using
   any real password-store contents or the owner's GPG identity.
2. Prepare a closed, bounded package containing the generated private backup
   export, public certificate, revocation material, inner key passphrase and
   encrypted canary. Declare every member and its integrity/size expectations.
   Revocation material is preserved; it is not a replacement for decryption
   material and is not applied to a real key.
3. Protect the package independently using a separately generated random outer
   secret, AES256-CFB with MDC and explicitly selected iterated/salted S2K parameters. Pass
   secrets through bounded stdin, never command arguments, environment variables
   or log output. No cryptographic primitive or secret-sharing scheme is coded.
4. Remove the source GPG home and stop its temporary agent before opening the
   recovery package in a new home. Import the recovered synthetic key material
   and decrypt the canary using the recovered inner passphrase. Verify the
   expected identity/subkey and exact non-sensitive plaintext.
5. Exercise rejection of a wrong outer secret, wrong inner passphrase, missing
   private decryption material and corrupted/truncated packages. No negative
   result may be silently counted as a successful recovery.
6. Report only non-secret conditions and remove temporary storage/agents. A
   generated outer secret held by the same fixture process is not independently
   held emergency custody, a second person or an offline recovery location.

Unlinking temporary files is not secure erasure of process memory or storage.
The enforced sandbox bounds this synthetic run; it does not qualify plaintext
handling for the eventual real operation.

The six payload members are `private-key.gpg`, `public-key.gpg`, `credential.gpg`,
`revocation.txt`, `recovery.json` and `instructions.txt`; `manifest.json` is the
only additional tar member. The recovery JSON contains the generated inner
passphrase, two fingerprint references and expected canary hash. The independent
outer secret is not included. No recursive extraction is performed.

| Bound | Implementation contract |
| --- | --- |
| Member | At most 256 KiB; fixed regular tar member, mode 0600 |
| Closed tar/ciphertext | At most 2 MiB for each artifact/input |
| GPG subprocess | 30-second deadline, combined stdout/stderr at most 2 MiB plus 64 KiB; stderr additionally limited to 64 KiB |
| Temporary GPG homes | Five: source, producer, outer, wrong-inner and recovered; no supplied host keyring |
| Generated secrets | Separate 256-bit random values encoded as 64 hexadecimal characters, supplied through stdin |

Every successful decryption must provide the expected AES256/MDC GPG status
markers, including `GOODMDC`, `DECRYPTION_OKAY`, `END_DECRYPTION` and
`DECRYPTION_INFO 2 9 0`. A negative check counts only a recognized GPG decryption
rejection; timeout, unavailable tools or another generic failure are not proof
that the wrong key or altered artifact was rejected correctly.

The success report uses `rbx.resilience.gpg-emergency-fixture.v1`, records the
actual GPG version/profile, artifact size, elapsed time, restored primary/subkey,
fresh recovery home, canary equality and five separate negative outcomes. It
keeps `production_key_accessed`, `provider_integration_tested`,
`threshold_implemented`, `independent_custody_tested`,
`production_security_support_qualified` and `cross_version_tested` false. Agent
cleanup and removal of the temporary directory are separate acknowledgements.
The dated execution checkpoint below records what actually passed.

The expected manifest hash is supplied separately to `unpack`, but the same
fixture process creates and holds it alongside the generated outer secret.
This does not establish independent provenance, trusted receipt custody or
resistance to jointly substituting ciphertext and expected hashes. The fixture
also does not invoke the real `pass` client or exercise Git replication of a
credential store.

## Tool choice and current security qualification

The first compatibility baseline reuses GnuPG already present in the pinned
fixture runtime. This is a bounded implementation choice for testing the format
and recovery path; it is not approval of a production emergency toolchain.

| Candidate | Current decision and remaining evidence |
| --- | --- |
| Existing GnuPG compatibility baseline | Exercise the generated export/import and symmetric package mechanism now; qualify exact versions, parameters, restore behavior and maintenance before real secrets |
| age passphrase wrapping | Future candidate; evaluate the v1.3.2 passphrase/scrypt interface, interoperability, resource bounds and recovery instructions in a separate synthetic comparison |
| Threshold release | No threshold, people, product or real release method is chosen; evaluate a maintained mechanism and its negative/availability tests separately |
| Trezor Python reference implementation | Not approved for sensitive real keys; its own README warns about that use. A reference algorithm implementation is not a production custody product |

The declared outer GPG profile is AES256, S2K mode 3, SHA256 and S2K count
65,011,712. These are explicit compatibility inputs to test, not a claim that
this document supplies a current production password-hardening recommendation.
The outer secret must remain independent of the GPG identity it protects; the
fixture's generated secret is not a proposed delivery or quorum procedure.

The maintenance comparison supplied for this increment is:

| Runtime | Version context | Limit on interpretation |
| --- | --- | --- |
| Pinned fixture container | GnuPG `2.2.40-1.1+deb12u2`; upstream 2.2 end of life was 2024-12-31 | Exact `gpg`/`gnupg` package version matches the published Debian Bookworm package; Debian records the CVE-2025-68973 fix in this revision. Bookworm LTS extends to 2028-06-30. This maintenance basis is checked; complete runtime/library/provenance qualification remains open |
| Observed host | GnuPG `2.4.4`; upstream 2.4 end of life was 2026-06-30 | Host version is not independent custody or production security qualification |
| Upstream download reference | Stable release listed as `2.5.24` at the current research checkpoint | Do not upgrade a host or image from this documentation; assess the exact intended deployment and distributor support |

An upstream branch's end-of-life date alone does not establish whether a
distribution package is vulnerable or unsupported by its distributor.
**Production toolchain security qualification remains OPEN.** These tests prove
only the mechanisms actually executed with the recorded runtime and parameters.

## Executed checkpoint: 2026-11-08

The rootless wrapper above completed with exit status 0 using runtime digest
`sha256:fc4a6037f035baa0d229bd86f744ceca1ddb720e672004d0b7ffad1209365124`,
Python 3.11.2 and GnuPG 2.2.40 (`gpg`/`gnupg`
`2.2.40-1.1+deb12u2`). It discovered 116 tests: **115 passed and one existing
PyYAML-dependent test was explicitly skipped** because the cached runtime does
not include that parser. The separate YAML CI job requires the dependency;
the local skip is not reported as a parser pass. The new 28 tests exercise
structure and mocked control flow, independently of the actual crypto drill.

All three wrapper CLIs passed: the existing local-copy recovery fixture, the
Headscale-shaped fixture and this GPG emergency fixture. The latter reported:

| Actual GPG check | Observed result |
| --- | --- |
| Source removed before recovery | Original temporary keyring removed and agent stopped |
| Fresh import and decryption | Generated primary key and encryption subkey restored; exact canary matched |
| Wrong outer secret / wrong inner passphrase | Both rejected by actual GPG decryption |
| Missing private key | Rejected by actual GPG decryption |
| Corruption / truncation | Both rejected by actual GPG decryption |
| Cleanup | All five temporary agents acknowledged closure; temporary tree removed |
| Artifact / measured interval | 20,551 encrypted bytes; reported crypto-drill interval 4.537 seconds, excluding final cleanup |

This measured interval is a synthetic fixture observation, not an operational
RTO. The single controller held the generated outer secret and expected hash;
no independent custody, remote backend or founder-absence test occurred.

Failure history is retained: the initial smoke attempt failed. A sanitized
diagnostic run located `package_member_bound`: GPG encryption with an input
filename had written its output to a file, leaving captured stdout empty.
Explicit `--output -` fixed the canary and envelope output paths. A subsequent
standalone crypto drill and then the complete wrapper passed. No real key,
credential store, external medium or cloud was involved in either outcome.

The final tested code bytes are identified independently of the documentation:

| File | SHA256 |
| --- | --- |
| `scripts/resilience/gpg_emergency_fixture.py` | `40f1a2aacde6a34ec20b4d3bc64f0b18d9b6a57819639739aa813fb85f4e9c2f` |
| `scripts/tests/test_resilience_gpg_emergency_fixture.py` | `e2364b8454f2f676a180afe51648e09853f0d07165190fe1e0cf7e7e1112d130` |
| `scripts/resilience/run-local-fixture.sh` | `337bd9b1a09b39f85abdc4c38f835bc44bd6f40630dc3b74a0a075d524d6a6ab` |

## Primary references and decision boundary

- [GnuPG download and support information](https://gnupg.org/download/index.html)
  supplies the upstream release/lifecycle comparison; distributor backports are
  a separate check.
- [Debian Bookworm GPG package](https://packages.debian.org/bookworm/gpg),
  [Debian 12 LTS announcement](https://www.debian.org/News/2026/20260712)
  and [Debian CVE-2025-68973 tracker](https://security-tracker.debian.org/tracker/CVE-2025-68973)
  supply the checked package revision, distribution support period and specific
  backport evidence. They do not qualify every dependency or the full image.
- [GnuPG operational commands](https://www.gnupg.org/documentation/manuals/gnupg/Operational-GPG-Commands.html),
  [OpenPGP options](https://www.gnupg.org/documentation/manuals/gnupg/OpenPGP-Options.html)
  and [input/output options](https://www.gnupg.org/documentation/manuals/gnupg/GPG-Input-and-Output.html)
  are the primary references for export/import, symmetric/S2K settings and
  passphrase input. They do not prove that any real package was prepared.
- [age v1.3.2 release documentation](https://github.com/FiloSottile/age/releases/tag/v1.3.2)
  is the comparison reference for its passphrase mode; no age implementation is
  exercised by this GPG fixture.
- [Trezor Python Shamir reference implementation](https://github.com/trezor/python-shamir-mnemonic)
  documents its implementation warning. This repository has selected neither
  that implementation nor a threshold for real secrets.

## Remaining real-recovery gates

Before handling real material, privately reconcile the exact private decryption
keys/subkeys and retained ciphertext generations, protected credential-store
replica, required inner passphrases, independently recoverable outer protection,
authorized custodians and practical access authority. Choose cloud/offline
locations, the RBX safe and an additional physical failure domain under the
reviewed custody plan. No location or custodian identity is invented here.

The actual operation needs reviewed finite bounds, plaintext containment,
toolchain security qualification, trusted receipt/provenance, independent
bootstrap/MFA and a dated open/decryption test from the intended environment.
Package copies and release material must have separate recovery dependencies.
An encrypted credential-store replica without usable private decryption
material, or private material whose only unlock instructions are inside that
same inaccessible store, does not establish recovery.

Actual production exports, protected media writes, replication and release
material delivery require their exact reviewed operation. A reconstructed secret
can be copied; an emergency threshold would not control already unlocked
operational keys. Founder absence, lost locations/custodians, insufficient quorum,
corruption, package generations and revocation/rotation need their own exercised
procedures. Keep every untested requirement visible after the fixture passes.
