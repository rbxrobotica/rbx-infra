"""Throwaway kustomizations, a stub docker and a stub pip; no registry, no
cluster and no PyPI.

Run with the interpreter of an environment made by
scripts/promote-image-tag-env.sh, as scripts/test-promote-image-tag.sh does:
the promotion script is given that same environment.
"""

import base64
import os
from pathlib import Path
import shutil
# The tests run the script under test and its verifier as processes.
import subprocess  # nosec B404
import sys
import tempfile
import unittest

import yaml

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "promote-image-tag.sh"
VERIFIER = REPO / "scripts" / "promote-image-tag-verify.py"
PROVISION = REPO / "scripts" / "promote-image-tag-env.sh"

# What a stub `python3 -m pip` does. promote-image-tag-env.sh asks it for its
# version first and then has it install into the python that follows --python.
PIP_MISSING = 'echo "No module named pip" >&2\nexit 1'
PIP_CANNOT_INSTALL = '[ "$3" = --version ] && exit 0\necho "no matching distribution" >&2\nexit 1'
PIP_INSTALLS_NOTHING = "exit 0"
# What a stub `python3 -m venv` leaves in the working directory before it fails.
VENV_STOPS_HALF_WAY = 'mkdir bin && echo "home = /nowhere" > pyvenv.cfg\necho "venv: simulated failure" >&2\nexit 1'

API = "ghcr.io/rbxrobotica/rbx-commerce"
WEB = "ghcr.io/rbxrobotica/rbx-commerce-web"
MIRROR = "ghcr.io/rbxrobotica/mirror"
OLD = "sha-1111111"
NEW = "sha-2222222"
DIGEST = "sha256:" + "a" * 64

DIGEST_REFUSAL = f"it pins {API} by digest; refusing to change newTag alone"
NO_SINGLE_NEW_TAG = "could not update exactly one newTag"

API_TAG_ONLY = f"  - name: {API}\n    newTag: {OLD}\n"
API_PINNED = (
    "  # The cluster pulls the API by digest.\n"
    f"  - name: {API}\n    newTag: {OLD}\n    digest: {DIGEST}\n"
)
WEB_TAG_ONLY = f"  - name: {WEB}\n    newTag: {OLD}\n"

HEAD = (
    "apiVersion: kustomize.config.k8s.io/v1beta1\n"
    "kind: Kustomization\n"
    "resources:\n"
    "  - deploy.yml\n"
)
TAIL = (
    "labels:\n"
    "  - pairs:\n"
    "      environment: production\n"
    "    includeSelectors: false\n"
)


def kustomization(images):
    return f"{HEAD}images:\n{images}{TAIL}"


def unseen_digests():
    """Kustomizations that give API a digest a line reader does not see, each
    with the reason the verifier refuses it.

    After a line edit of the first newTag, kustomize 5.0.4 still runs a
    container of API by the old digest in each of them, and with no tag at all
    in the last.
    """
    encoded = base64.b64encode(API.encode()).decode()
    escaped = API.replace("commerce", "co\\u006dmerce")
    second = f"  - digest: {DIGEST}\n    name: {API}\n"
    return {
        "key in upper case": (
            kustomization(f"{API_TAG_ONLY}    Digest: {DIGEST}\n"),
            "an images entry has the key 'Digest'"),
        "key with a long s": (
            kustomization(f"{API_TAG_ONLY}    dige\u017ft: {DIGEST}\n"),
            "an images entry has the key 'dige\\u017ft'"),
        "merge key": (
            kustomization(f"{API_TAG_ONLY}    <<: {{digest: {DIGEST}}}\n"),
            "an images entry has the key '<<'"),
        "second entry, keys reversed": (
            kustomization(API_TAG_ONLY + second),
            f"2 of its images entries name {API}"),
        "second entry, flow style": (
            kustomization(f"{API_TAG_ONLY}  - {{name: {API}, digest: {DIGEST}}}\n"),
            f"2 of its images entries name {API}"),
        "second entry, escaped name": (
            kustomization(f'{API_TAG_ONLY}  - digest: {DIGEST}\n    name: "{escaped}"\n'),
            "the name of an images entry is not written out in full"),
        "second entry, binary name": (
            kustomization(f"{API_TAG_ONLY}  - digest: {DIGEST}\n    name: !!binary {encoded}\n"),
            "the name of an images entry is not written out in full"),
        "second entry, alias": (
            kustomization(f"  - &pinned\n    digest: {DIGEST}\n    name: {API}\n{API_TAG_ONLY}  - *pinned\n"),
            "it uses a YAML alias"),
        "second entry, a dot where the name has a hyphen": (
            kustomization(f"{API_TAG_ONLY}  - digest: {DIGEST}\n    name: {API.replace('rbx-', 'rbx.')}\n"),
            f"its images entry for {API.replace('rbx-', 'rbx.')} also matches {API}"),
        "second entry, a dot where the reference has its colon": (
            kustomization(f"{API_TAG_ONLY}  - digest: {DIGEST}\n    name: {API}.{NEW}\n"),
            f"its images entry for {API}.{NEW} also matches {API}"),
        "second entry, tag in the name": (
            kustomization(f"{API_TAG_ONLY}  - digest: {DIGEST}\n    name: {API}:{NEW}\n"),
            "the name of an images entry is not a plain image name"),
        "second entry, pattern in the name": (
            kustomization(f"{API_TAG_ONLY}  - digest: {DIGEST}\n    name: {API[:-4]}.*\n"),
            "the name of an images entry is not a plain image name"),
        "second entry for the new name of the first": (
            kustomization(f"  - name: {API}\n    newName: {MIRROR}\n    newTag: {OLD}\n"
                          f"  - digest: {DIGEST}\n    name: {MIRROR}\n"),
            "an images entry renames an image with newName"),
        "second entry renaming another image to this one": (
            kustomization(f"{API_TAG_ONLY}  - digest: {DIGEST}\n    newName: {API}\n    name: {WEB}\n"),
            "an images entry renames an image with newName"),
        "deprecated imageTags": (
            kustomization(API_TAG_ONLY) + f"imageTags:\n{second}",
            "it uses the deprecated imageTags list"),
        "deprecated imageTags in upper case": (
            kustomization(API_TAG_ONLY) + f"ImageTags:\n{second}",
            "it uses the deprecated imageTags list"),
        "deprecated imageTags with a long s": (
            kustomization(API_TAG_ONLY) + f"imageTag\u017f:\n{second}",
            "it uses the deprecated imageTags list"),
        "second images key, repeated": (
            kustomization(API_TAG_ONLY) + f"images:\n{second}",
            "two of its top-level keys differ at most in letter case"),
        "second images key in upper case": (
            kustomization(API_TAG_ONLY) + f"Images:\n{second}",
            "two of its top-level keys differ at most in letter case"),
        "second images key with a long s": (
            kustomization(API_TAG_ONLY) + f"image\u017f:\n{second}",
            "two of its top-level keys differ at most in letter case"),
        "second images key, escaped": (
            kustomization(API_TAG_ONLY) + f'"i\\u006dages":\n{second}',
            "a top-level key is not written out in full"),
        "second images key under a top-level merge key": (
            kustomization(API_TAG_ONLY) + f"<<: {{Images: [{{name: {API}, digest: {DIGEST}}}]}}\n",
            "it has a top-level merge key"),
        "images block inside a quoted scalar": (
            f'{HEAD}namespace: "demo\nimages:\n{API_TAG_ONLY}labels: production"\n',
            "it has no top-level key spelled images"),
    }


class PromoteImageTagTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.scratch = self.root / "tmp"
        self.scratch.mkdir()
        self.docker_log = self.root / "docker.log"

    def overlay(self, text, app="demo"):
        path = self.root / "apps" / "prod" / app / "kustomization.yml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def promote(self, image, app="demo", **extra):
        environment = {key: value for key, value in os.environ.items()
                       if key not in ("ALLOW_PARTIAL_MULTI_IMAGE", "CHECK_REGISTRY")}
        environment["TMPDIR"] = str(self.scratch)
        environment["PROMOTE_IMAGE_TAG_ENV"] = sys.prefix
        environment.update(extra)
        # The argv is fixed and bash comes from PATH, as in image-update.yml.
        result = subprocess.run(  # nosec B603 B607
            ["bash", str(SCRIPT), app, image, NEW], cwd=self.root, env=environment,
            capture_output=True, encoding="utf-8", check=False)
        self.assertEqual(list(self.scratch.iterdir()), [], "temporary file left behind")
        return result

    def stub(self, command, body):
        bin_dir = self.root / "bin"
        bin_dir.mkdir(exist_ok=True)
        path = bin_dir / command
        path.write_text(f"#!/bin/sh\n{body}\n")
        path.chmod(0o755)
        return {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}

    def stub_docker(self):
        return {**self.stub("docker", 'printf "%s\\n" "$*" >> "$DOCKER_LOG"\nexit "$DOCKER_EXIT"'),
                "CHECK_REGISTRY": "1", "DOCKER_LOG": str(self.docker_log)}

    def provision(self, pip, target, venv=None):
        """Run promote-image-tag-env.sh with a python3 whose pip, and whose venv
        if given, is these shell lines; every other python3 call reaches this
        interpreter."""
        python3 = f'if [ "$1" = -m ] && [ "$2" = pip ]; then\n{pip}\nfi\n'
        if venv:
            python3 += f'if [ "$1" = -m ] && [ "$2" = venv ]; then\n{venv}\nfi\n'
        python3 += f'exec "{sys.executable}" "$@"'
        # LC_ALL: the tests read what mkdir says when it fails.
        environment = {**os.environ, **self.stub("python3", python3), "LC_ALL": "C"}
        # The argv is fixed and bash comes from PATH, as in image-update.yml.
        return subprocess.run(  # nosec B603 B607
            ["bash", str(PROVISION), str(target)], cwd=self.root, env=environment,
            capture_output=True, encoding="utf-8", check=False)

    def assert_promoted(self, result, path, before, image):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, f"Updated apps/prod/demo/kustomization.yml: {image}:{NEW}\n")
        old_stanza = f"  - name: {image}\n    newTag: {OLD}\n"
        self.assertEqual(before.count(old_stanza), 1)
        self.assertEqual(path.read_text(encoding="utf-8"),
                         before.replace(old_stanza, f"  - name: {image}\n    newTag: {NEW}\n"))

    def assert_removed(self, result, target, reason):
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(reason, result.stderr)
        self.assertIn(f"provisioning failed; removed {target}\n", result.stderr)
        self.assertNotIn("already exists", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertFalse(os.path.lexists(target))

    def assert_theirs(self, directory):
        """The directory someone else made holds what they put in it, and no more."""
        self.assertEqual([entry.name for entry in directory.iterdir()], ["kept.txt"])
        self.assertEqual((directory / "kept.txt").read_text(encoding="utf-8"), "theirs\n")

    def assert_refused(self, result, path, before, reason, code=2):
        self.assertEqual(result.returncode, code, result.stderr)
        self.assertIn(reason, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_tag_only_entry_gets_the_new_tag_and_nothing_else_changes(self):
        before = kustomization("  # Bumped by CI.\n" + API_TAG_ONLY)
        path = self.overlay(before)
        self.assert_promoted(self.promote(API), path, before, API)

    def test_digest_pinned_entry_is_refused_wherever_the_digest_sits(self):
        stanzas = {
            "after newTag": API_PINNED,
            "before newTag": f"  - name: {API}\n    digest: {DIGEST}\n    newTag: {OLD}\n",
            "after a comment": f"{API_TAG_ONLY}    # verified pull\n    digest: {DIGEST}\n",
            "after a blank line": f"{API_TAG_ONLY}\n    digest: {DIGEST}\n",
            "empty": f"{API_TAG_ONLY}    digest:\n",
        }
        for case, stanza in stanzas.items():
            with self.subTest(case=case):
                before = kustomization(stanza)
                path = self.overlay(before)
                result = self.promote(API)
                self.assert_refused(result, path, before, DIGEST_REFUSAL)
                self.assertIn("set newTag and the digest of that tag in the same commit", result.stderr)

    def test_lists_indented_like_their_key_are_read_the_same_way(self):
        tag_only = f"images:\n- name: {API}\n  newTag: {OLD}\nlabels:\n- pairs:\n    environment: production\n"
        path = self.overlay(tag_only)
        self.assertEqual(self.promote(API).returncode, 0)
        self.assertEqual(path.read_text(encoding="utf-8"), tag_only.replace(OLD, NEW))

        pinned = tag_only.replace("labels:", f"  digest: {DIGEST}\nlabels:")
        path = self.overlay(pinned)
        self.assert_refused(self.promote(API), path, pinned, DIGEST_REFUSAL)

    def test_digest_a_line_reader_does_not_see_is_refused(self):
        for case, (before, reason) in unseen_digests().items():
            for flag in ("0", "1"):
                with self.subTest(case=case, allow_partial=flag):
                    path = self.overlay(before)
                    result = self.promote(API, ALLOW_PARTIAL_MULTI_IMAGE=flag)
                    self.assert_refused(result, path, before, reason)
                    self.assertIn("apps/prod/demo/kustomization.yml: ", result.stderr)

    def test_kustomization_the_verifier_cannot_read_is_refused(self):
        cases = {
            "unknown key in the entry": (
                kustomization(f"{API_TAG_ONLY}    extra:\n      - name: nested\n"),
                "an images entry has the key 'extra'"),
            "newTag twice": (
                kustomization(f"{API_TAG_ONLY}    newTag: {OLD}\n"),
                "an images entry has the key 'newTag' twice"),
            "entry without a name": (
                kustomization(f"{API_TAG_ONLY}  - newTag: {OLD}\n"),
                "an images entry has no name"),
            "entry that is not a mapping": (
                kustomization(f"{API_TAG_ONLY}  - {WEB}\n"),
                "an images entry is not a mapping"),
            "images tagged as another collection": (
                kustomization(API_TAG_ONLY).replace("images:\n", "images: !!omap\n"),
                "images is not a list"),
            "two documents": (
                kustomization(API_TAG_ONLY) + "---\nkind: Kustomization\n",
                "it cannot be read as one YAML document (ComposerError)"),
            "tab in the indentation": (
                kustomization(API_TAG_ONLY) + "patches:\n\t- path: patch.yml\n",
                "it cannot be read as one YAML document (ScannerError)"),
        }
        for case, (before, reason) in cases.items():
            with self.subTest(case=case):
                path = self.overlay(before)
                self.assert_refused(self.promote(API, ALLOW_PARTIAL_MULTI_IMAGE="1"), path, before, reason)

    def test_comments_and_a_tag_suffix_do_not_block(self):
        extras = f"    # digest: {DIGEST}\n    tagSuffix: -slim\n"
        before = kustomization(f"  # {API} is built by its own CI.\n{API_TAG_ONLY}{extras}")
        before = before.replace("kind: Kustomization\n", f"kind: Kustomization  # pins {API}\n")
        path = self.overlay(before)
        self.assert_promoted(self.promote(API), path, before, API)

    def test_multi_image_kustomization_needs_the_explicit_flag(self):
        before = kustomization(API_TAG_ONLY + WEB_TAG_ONLY)
        path = self.overlay(before)
        self.assert_refused(self.promote(API), path, before,
                            "declares 2 images; refusing partial promotion without ALLOW_PARTIAL_MULTI_IMAGE=1")
        self.assert_promoted(self.promote(API, ALLOW_PARTIAL_MULTI_IMAGE="1"), path, before, API)

    def test_partial_promotion_judges_only_the_target_entry(self):
        for case, images in {"pinned first": API_PINNED + WEB_TAG_ONLY,
                             "pinned last": WEB_TAG_ONLY + API_PINNED}.items():
            with self.subTest(case=case):
                before = kustomization(images)
                path = self.overlay(before)
                self.assert_refused(self.promote(API, ALLOW_PARTIAL_MULTI_IMAGE="1"), path, before,
                                    DIGEST_REFUSAL)
                self.assert_promoted(self.promote(WEB, ALLOW_PARTIAL_MULTI_IMAGE="1"), path, before, WEB)
                self.assertIn(f"  - name: {API}\n    newTag: {OLD}\n    digest: {DIGEST}\n", path.read_text(encoding="utf-8"))

    def test_entry_without_one_new_tag_is_left_alone(self):
        stanzas = {
            "newName only": f"  - name: {API}\n    newName: {MIRROR}\n",
            "digest only": f"  - name: {API}\n    digest: {DIGEST}\n",
            "named in a comment and inside a longer name": f"  # - name: {API}\n{WEB_TAG_ONLY}",
        }
        for case, stanza in stanzas.items():
            with self.subTest(case=case):
                before = kustomization(stanza)
                path = self.overlay(before)
                self.assert_refused(self.promote(API), path, before, NO_SINGLE_NEW_TAG, code=42)

    def test_registry_check_gates_the_write(self):
        before = kustomization(API_TAG_ONLY)
        path = self.overlay(before)
        registry = self.stub_docker()
        self.assert_refused(self.promote(API, DOCKER_EXIT="1", **registry), path, before,
                            f"image tag does not exist or is not readable: {API}:{NEW}", code=1)
        self.assert_promoted(self.promote(API, DOCKER_EXIT="0", **registry), path, before, API)
        self.assertEqual(self.docker_log.read_text(), f"manifest inspect {API}:{NEW}\n" * 2)

    def test_digest_pinned_entry_is_refused_after_a_passing_registry_check(self):
        before = kustomization(API_PINNED)
        path = self.overlay(before)
        self.assert_refused(self.promote(API, DOCKER_EXIT="0", **self.stub_docker()), path, before,
                            DIGEST_REFUSAL)

    def test_edit_made_while_the_registry_answers_is_judged_and_kept(self):
        before = kustomization(API_TAG_ONLY)
        path = self.overlay(before)
        pinned = self.root / "pinned.yml"
        pinned.write_text(kustomization(API_PINNED), encoding="utf-8")
        registry = {**self.stub("docker", f'cp "{pinned}" "{path}"'), "CHECK_REGISTRY": "1"}
        self.assert_refused(self.promote(API, **registry), path, kustomization(API_PINNED), DIGEST_REFUSAL)

    def test_renamed_image_is_refused(self):
        before = kustomization(f"  - name: {API}\n    newName: {MIRROR}\n    newTag: {OLD}\n")
        path = self.overlay(before)
        self.assert_refused(self.promote(API), path, before, "an images entry renames an image with newName")

    def test_unrelated_names_do_not_block(self):
        others = (
            f"  - name: {API}-web\n    newTag: {OLD}\n    digest: {DIGEST}\n"
            f"  - name: {API[:-1]}\n    digest: {DIGEST}\n"
            f"  - name: {API}/sidecar\n    digest: {DIGEST}\n"
            f"  - name: clickhouse/clickhouse-server\n    newTag: 24.12-alpine\n"
        )
        before = kustomization(API_TAG_ONLY + others)
        path = self.overlay(before)
        self.assert_promoted(self.promote(API, ALLOW_PARTIAL_MULTI_IMAGE="1"), path, before, API)

    def test_environment_that_was_not_provisioned_stops_before_the_edit(self):
        before = kustomization(API_TAG_ONLY)
        path = self.overlay(before)
        empty = self.root / "empty"
        empty.mkdir()
        other_pins = self.root / "other-pins"
        (other_pins / "bin").mkdir(parents=True)
        (other_pins / "bin" / "python").symlink_to(sys.executable)
        (other_pins / "promote-image-tag-requirements.sha256").write_text("0" * 64 + "  -\n", encoding="utf-8")
        for case, directory in {"unset": "", "without an interpreter": str(empty),
                                "provisioned from other pins": str(other_pins)}.items():
            with self.subTest(case=case):
                self.assert_refused(self.promote(API, PROMOTE_IMAGE_TAG_ENV=directory), path, before,
                                    "PROMOTE_IMAGE_TAG_ENV is not an environment of the current")

    def test_provisioned_environment_is_kept_and_the_promotion_accepts_it(self):
        target = self.root / "environment"
        # In place of a download: this environment's PyYAML, copied into the new one.
        copies = ('[ "$3" = --version ] && exit 0\n'
                  f'cp -r "{Path(yaml.__file__).parent}" '
                  '"$("$4" -c \'import sysconfig; print(sysconfig.get_path("purelib"))\')"\n'
                  "exit")
        result = self.provision(copies, target.name)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, f"python {sys.version.split()[0]} PyYAML {yaml.__version__}\n")
        self.assertNotIn("removed", result.stderr)
        before = kustomization(API_TAG_ONLY)
        path = self.overlay(before)
        self.assert_promoted(self.promote(API, PROMOTE_IMAGE_TAG_ENV=str(target)), path, before, API)

    def test_provisioning_that_fails_leaves_no_directory_and_can_be_retried(self):
        target = self.root / "environment"
        cases = {
            "pip cannot install": (PIP_CANNOT_INSTALL, target, "no matching distribution"),
            "pip cannot install, relative path": (PIP_CANNOT_INSTALL, target.name, "no matching distribution"),
            "PyYAML is missing after the install": (PIP_INSTALLS_NOTHING, target, "No module named 'yaml'"),
        }
        for case, (pip, argument, reason) in cases.items():
            for attempt in ("first", "second"):
                with self.subTest(case=case, attempt=attempt):
                    self.assert_removed(self.provision(pip, argument), target, reason)

    def test_provisioning_that_fails_at_the_venv_or_at_the_marker_leaves_no_directory(self):
        target = self.root / "environment"
        for attempt in ("first", "second"):
            with self.subTest(case="venv stops half way", attempt=attempt):
                result = self.provision(PIP_INSTALLS_NOTHING, target, venv=VENV_STOPS_HALF_WAY)
                self.assert_removed(result, target, "venv: simulated failure")
        self.stub("sha256sum", 'echo "sha256sum: simulated failure" >&2\nexit 1')
        for attempt in ("first", "second"):
            with self.subTest(case="the marker cannot be written", attempt=attempt):
                self.assert_removed(self.provision(PIP_INSTALLS_NOTHING, target), target,
                                    "sha256sum: simulated failure")

    def test_provisioning_without_pip_or_a_parent_directory_creates_nothing(self):
        cases = {
            "python3 has no pip": (PIP_MISSING, self.root / "environment", 2,
                                   "python3 -m pip is not available on this machine; nothing was created"),
            "the parent directory is missing": (PIP_CANNOT_INSTALL, self.root / "missing" / "environment", 1,
                                                "No such file or directory"),
        }
        for case, (pip, target, code, reason) in cases.items():
            with self.subTest(case=case):
                result = self.provision(pip, target)
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertIn(reason, result.stderr)
                self.assertNotIn("removed", result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertEqual(sorted(entry.name for entry in self.root.iterdir()), ["bin", "tmp"])

    def test_provisioning_refuses_what_exists_and_leaves_it_alone(self):
        directory = self.root / "directory"
        directory.mkdir()
        (directory / "kept.txt").write_text("kept\n", encoding="utf-8")
        regular = self.root / "regular"
        regular.write_text("kept\n", encoding="utf-8")
        dangling = self.root / "dangling"
        dangling.symlink_to(self.root / "nowhere")
        for case, target in {"a directory": directory, "a file": regular, "a dangling link": dangling}.items():
            with self.subTest(case=case):
                result = self.provision(PIP_CANNOT_INSTALL, target)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stderr, f"already exists: {target}\n")
                self.assertEqual(result.stdout, "")
        self.assertEqual((directory / "kept.txt").read_text(encoding="utf-8"), "kept\n")
        self.assertEqual(regular.read_text(encoding="utf-8"), "kept\n")
        self.assertTrue(dangling.is_symlink())
        self.assertFalse(os.path.lexists(self.root / "nowhere"))

    def test_directory_that_appears_before_the_mkdir_is_not_removed(self):
        target = self.root / "environment"
        # pip is asked for its version after the script saw no directory and
        # before the script creates one: someone else creates it in between.
        appears = (f'[ "$3" = --version ] && mkdir "{target}" && echo theirs > "{target}/kept.txt" && exit 0\n'
                   "exit 1")
        result = self.provision(appears, target)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("File exists", result.stderr)
        self.assertNotIn("removed", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assert_theirs(target)

    def test_directory_that_is_not_empty_right_after_creation_is_left_alone(self):
        target = self.root / "environment"
        self.stub("mkdir", f'"{shutil.which("mkdir")}" "$@" && echo theirs > "{target}/kept.txt"')
        result = self.provision(PIP_CANNOT_INSTALL, target)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stderr,
                         f"{target} was not empty right after this run created it; nothing was removed\n")
        self.assertEqual(result.stdout, "")
        self.assert_theirs(target)

    def test_cleanup_leaves_a_directory_that_took_the_place_of_its_own(self):
        target = self.root / "environment"
        moved = self.root / "moved"
        # The stub fails right after the swap. What venv or pip would write
        # through the path after a swap is not something the script prevents.
        replaces = ('[ "$3" = --version ] && exit 0\n'
                    f'mv "{target}" "{moved}" && mkdir "{target}" && echo theirs > "{target}/kept.txt"\n'
                    "exit 1")
        result = self.provision(replaces, target)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(f"provisioning failed; {target} no longer names the directory this run was working in, "
                      "so nothing was removed\n", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assert_theirs(target)
        self.assertTrue((moved / "pyvenv.cfg").is_file())

    def test_cleanup_leaves_a_directory_behind_a_repointed_link(self):
        first, second, link = self.root / "first", self.root / "second", self.root / "link"
        first.mkdir()
        (second / "environment").mkdir(parents=True)
        (second / "environment" / "kept.txt").write_text("theirs\n", encoding="utf-8")
        link.symlink_to(first)
        target = link / "environment"
        repoints = f'[ "$3" = --version ] && exit 0\nln -sfn "{second}" "{link}"\nexit 1'
        result = self.provision(repoints, target)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(f"provisioning failed; {target} no longer names the directory this run was working in, "
                      "so nothing was removed\n", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assert_theirs(second / "environment")
        self.assertTrue((first / "environment" / "pyvenv.cfg").is_file())

    def test_cleanup_leaves_a_directory_that_took_the_place_after_its_check(self):
        target = self.root / "environment"
        moved = self.root / "moved"
        # The cleanup calls find -delete after it saw that the path still names
        # its directory: the replacement happens between the two.
        self.stub("find", 'case " $* " in *" -delete "*)\n'
                          f'  mv "{target}" "{moved}" && mkdir "{target}" && echo theirs > "{target}/kept.txt" ;;\n'
                          f'esac\nexec "{shutil.which("find")}" "$@"')
        result = self.provision(PIP_CANNOT_INSTALL, target)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("Directory not empty", result.stderr)
        self.assertNotIn("removed", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assert_theirs(target)
        self.assertEqual(list(moved.iterdir()), [])

    def test_verifier_accepts_the_expected_edit_and_nothing_more(self):
        before = kustomization(API_TAG_ONLY + WEB_TAG_ONLY)
        path = self.overlay(before)
        promoted = before.replace(API_TAG_ONLY, API_TAG_ONLY.replace(OLD, NEW))
        copies = {
            "the newTag of the image": (promoted, 0),
            "nothing": (before, 2),
            "another tag": (promoted.replace(OLD, "sha-3333333"), 2),
            "the newTag of both images": (before.replace(OLD, NEW), 2),
            "a resource as well": (promoted.replace("deploy.yml", "other.yml"), 2),
            "a digest as well": (promoted.replace(WEB_TAG_ONLY, f"{WEB_TAG_ONLY}    digest: {DIGEST}\n"), 2),
        }
        for case, (text, code) in copies.items():
            with self.subTest(changed=case):
                edited = self.root / "edited.yml"
                edited.write_text(text, encoding="utf-8")
                # The argv is fixed: this interpreter, the verifier and the two fixtures.
                result = subprocess.run(  # nosec B603
                    [sys.executable, "-I", "-B", str(VERIFIER), str(path), str(edited), API, NEW],
                    capture_output=True, encoding="utf-8", check=False)
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertEqual(path.read_text(encoding="utf-8"), before)
                self.assertEqual(edited.read_text(encoding="utf-8"), text)

    def test_every_prod_overlay_entry_is_promoted_alone_or_refused_for_its_digest(self):
        promoted = 0
        for source in sorted((REPO / "apps" / "prod").glob("*/kustomization.yml")):
            app = source.parent.name
            before = source.read_text(encoding="utf-8")
            for index, entry in enumerate(yaml.safe_load(before).get("images") or []):
                if not entry["name"].startswith("ghcr.io/rbxrobotica/"):
                    continue
                with self.subTest(app=app, image=entry["name"]):
                    path = self.overlay(before, app=app)
                    result = self.promote(entry["name"], app=app, ALLOW_PARTIAL_MULTI_IMAGE="1")
                    after = path.read_text(encoding="utf-8")
                    if "digest" in entry:
                        self.assertEqual(result.returncode, 2, result.stderr)
                        self.assertIn(f"it pins {entry['name']} by digest", result.stderr)
                        self.assertEqual(after, before)
                        continue
                    self.assertEqual(result.returncode, 0, result.stderr)
                    expected = yaml.safe_load(before)
                    expected["images"][index]["newTag"] = NEW
                    self.assertEqual(yaml.safe_load(after), expected)
                    old_lines, new_lines = before.splitlines(), after.splitlines()
                    self.assertEqual(len(new_lines), len(old_lines))
                    self.assertEqual(sum(old != new for old, new in zip(old_lines, new_lines)), 1)
                    promoted += 1
        self.assertGreater(promoted, 0)


if __name__ == "__main__":
    unittest.main()
