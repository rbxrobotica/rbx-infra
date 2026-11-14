"""Throwaway exports of two commits; no git, no registry and no cluster.

Run with the interpreter of an environment made by
scripts/promote-image-tag-env.sh, as scripts/test-promote-image-tag.sh does:
scripts/check-digest-pin-promotion.sh runs the check in that same environment.
The fixtures of test_promote_image_tag.py are reused. A spelling of a digest
that the promotion script refuses is one this check must not pass either.
kustomize_images_model.py says what kustomize renders for random lists.
"""

import contextlib
import hashlib
import importlib.util
import io
from pathlib import Path
import random
# The tests run the check as a process, the way the shell script does.
import subprocess  # nosec B404
import sys
import tempfile
import unittest

import yaml

import kustomize_images_model as model
import test_promote_image_tag as promotion

REPO = Path(__file__).resolve().parents[2]
CHECK = REPO / "scripts" / "check-digest-pin-promotion.py"
KUSTOMIZATION_NAMES = ("kustomization.yaml", "kustomization.yml", "Kustomization")

API, WEB, MIRROR = promotion.API, promotion.WEB, promotion.MIRROR
OLD, NEW, DIGEST = promotion.OLD, promotion.NEW, promotion.DIGEST
NEW_DIGEST = "sha256:" + "b" * 64
WEB_DIGEST = "sha256:" + "c" * 64
API_TAG_ONLY, API_PINNED, WEB_TAG_ONLY = promotion.API_TAG_ONLY, promotion.API_PINNED, promotion.WEB_TAG_ONLY
WEB_PINNED = f"{WEB_TAG_ONLY}    digest: {WEB_DIGEST}\n"
# The pinned entry twice, the second time through an alias.
ALIASED = f"  - &entry\n    name: {API}\n    newTag: {OLD}\n    digest: {DIGEST}\n  - *entry\n"
DOTTED = API.replace("rbx-", "rbx.")
kustomization = promotion.kustomization

PATH = "apps/prod/demo/kustomization.yml"
KEPT = f"{PATH}: {API}: newTag went from '{OLD}' to '{NEW}' and digest stayed '{DIGEST}'"
SAME_IMAGE = "can apply to the same image, one after another, and one of them pins a digest"
UNKNOWN_BASE = f"{PATH}: {API}: nothing shows that digest '{DIGEST}' is new beside newTag '{NEW}': in the base commit, "
UNDECIDED_BASE = (UNKNOWN_BASE + "several images entries can apply to that image, one after another, and one of them "
                  "pins a digest\n")
NO_LINK = ", and this check follows no link"


def link_at(path, mode="120000"):
    return (f"{path}: a symbolic link or a submodule (git mode {mode}): kustomize may read through it, and this check "
            "does not follow it\n")
WHY = "so a new tag beside the old digest keeps running the old image"


# What the lists of the model test are made of: names that match one another as
# patterns, a dot for the colon of a tag or the at sign of a digest, renames.
MODEL_NAMES = ("x/app", "x/app", "x/ap.", "x/app.v1", "x/app.v2", "x/app.web", "x/app-web", "x/mirror", "x/mirror",
               "x/mir.or", "x/bridge", "x/mirror.v1", "x/mirror.v2", "x/app.v1.sha256." + "a" * 64,
               "x/mirror.v2.sha256." + "b" * 64)
MODEL_NEW_NAMES = ("x/app", "x/mirror", "x/mirror", "x/bridge", "x/app-web", "x/mir.or")
MODEL_TAGS = ("v1", "v2", "v3")
MODEL_DIGESTS = (DIGEST, NEW_DIGEST)
# The image references a manifest could have. None carries a digest of its own.
MODEL_REFERENCES = tuple(name + tag for name in ("x/app", "x/apq", "x/app-web", "x/app.web", "x/mirror", "x/mir-or",
                                                 "x/mir.or", "x/bridge")
                         for tag in ("", ":v1", ":v2", ":v3", ":web"))


def model_entry(rng):
    entry = {"name": rng.choice(MODEL_NAMES)}
    if rng.random() < 0.3:
        entry["newName"] = rng.choice(MODEL_NEW_NAMES)
    if rng.random() < 0.7:
        # Now and then a tag that carries a digest behind an at sign.
        entry["newTag"] = rng.choice(MODEL_TAGS) + ("@" + rng.choice(MODEL_DIGESTS) if rng.random() < 0.03 else "")
    if rng.random() < 0.5:
        entry["digest"] = rng.choice(MODEL_DIGESTS)
    if rng.random() < 0.08:
        entry["tagSuffix"] = "-s"
    return entry


def model_change(rng, base):
    """A list after one to three edits of the kind a promotion, a repair or a slip makes."""
    head = [dict(entry) for entry in base]
    for _ in range(rng.randint(1, 3)):
        action = rng.choice(("tag", "tag", "tag", "digest", "drop digest", "add digest", "drop tag", "remove", "insert",
                             "swap", "name", "rename", "drop rename"))
        index = rng.randrange(len(head)) if head else 0
        if action == "insert" or not head:
            head.insert(rng.randint(0, len(head)), model_entry(rng))
        elif action == "tag":
            head[index]["newTag"] = rng.choice(MODEL_TAGS)
        elif action == "digest":
            head[index]["digest"] = rng.choice(MODEL_DIGESTS)
        elif action == "add digest":
            head[index].setdefault("digest", rng.choice(MODEL_DIGESTS))
        elif action == "drop digest":
            head[index].pop("digest", None)
        elif action == "drop tag":
            head[index].pop("newTag", None)
        elif action == "remove":
            del head[index]
        elif action == "swap":
            other = rng.randrange(len(head))
            head[index], head[other] = head[other], head[index]
        elif action == "name":
            head[index]["name"] = rng.choice(MODEL_NAMES)
        elif action == "rename":
            head[index]["newName"] = rng.choice(MODEL_NEW_NAMES)
        else:
            head[index].pop("newName", None)
    return head


def model_chain(rng):
    """A base that reaches a pinned entry through renames, and a head that says the same in one entry."""
    steps = rng.sample(("x/app", "x/bridge", "x/mirror", "x/app-web"), rng.randint(2, 4))
    digest = rng.choice(MODEL_DIGESTS)
    base = [{"name": one, "newName": other} for one, other in zip(steps, steps[1:])]
    base.append({"name": steps[-1], "newTag": rng.choice(MODEL_TAGS), "digest": digest})
    if rng.random() < 0.3:
        base.insert(rng.randint(0, len(base)), model_entry(rng))
    head = [{"name": steps[0], "newName": steps[-1], "newTag": rng.choice(MODEL_TAGS),
             "digest": digest if rng.random() < 0.7 else rng.choice(MODEL_DIGESTS)}]
    if rng.random() < 0.3:
        head = base[:rng.randint(0, len(base) - 1)] + head
    return base, head


def model_fault(base, head):
    """A reference that the head renders with a tag beside a digest the base rendered with another tag, or none."""
    for reference in MODEL_REFERENCES:
        _, tag, digest = model.split(model.apply(head, reference))
        _, old_tag, old_digest = model.split(model.apply(base, reference))
        if tag and digest and old_digest == digest and old_tag != tag:
            return reference
    return None


def promoted(text, tag=NEW, digest=None):
    """The kustomization after an edit of its first newTag, and of every digest."""
    text = text.replace(f"newTag: {OLD}", f"newTag: {tag}", 1)
    return text if digest is None else text.replace(DIGEST, digest)


class CheckDigestPinPromotionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.export = Path(temporary.name)
        (self.export / "blobs").mkdir()

    def commit(self, side, files):
        """Export one commit: path -> contents, text or bytes, or (git mode, contents)."""
        records = []
        for path, contents in files.items():
            mode, data = contents if isinstance(contents, tuple) else ("100644", contents)
            data = data if isinstance(data, bytes) else data.encode("utf-8")
            # A git object id, which names a blob and guards nothing.
            object_id = hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()
            kind = "commit" if mode == "160000" else "blob"
            if kind == "blob":
                (self.export / "blobs" / object_id).write_bytes(data)
            records.append(f"{mode} {kind} {object_id}\t{path}\0".encode("utf-8"))
        (self.export / f"{side}.tree").write_bytes(b"".join(records))

    def check(self, base, head):
        self.commit("base", base)
        self.commit("head", head)
        # The argv is fixed: this interpreter, the check and the export.
        return subprocess.run(  # nosec B603
            [sys.executable, "-I", "-B", str(CHECK), str(self.export)],
            capture_output=True, encoding="utf-8", check=False)

    def step(self, before, after):
        """The check over one kustomization that went from one text to another."""
        return self.check({} if before is None else {PATH: before}, {PATH: after})

    def assert_passes(self, result, changed=1):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout.count("ok: "), changed, result.stdout)
        self.assertIn(f"{changed} changed kustomization file(s), none with a new tag beside an old digest\n",
                      result.stdout)

    def assert_refused(self, result, *reasons):
        self.assertEqual(result.returncode, 1, result.stderr)
        for reason in reasons:
            self.assertIn(reason, result.stderr)
        self.assertIn(WHY, result.stderr)
        self.assertNotIn("ok: ", result.stdout)
        self.assertNotIn("none with a new tag", result.stdout)

    def test_new_tag_beside_the_old_digest_is_refused_wherever_the_digest_sits(self):
        stanzas = {
            "after newTag": API_PINNED,
            "before newTag": f"  - name: {API}\n    digest: {DIGEST}\n    newTag: {OLD}\n",
            "after a comment": f"{API_TAG_ONLY}    # verified pull\n    digest: {DIGEST}\n",
            "after a blank line": f"{API_TAG_ONLY}\n    digest: {DIGEST}\n",
            "in flow style": f"  - {{name: {API}, newTag: {OLD}, digest: '{DIGEST}'}}\n",
        }
        for case, stanza in stanzas.items():
            with self.subTest(case=case):
                before = kustomization(stanza)
                result = self.step(before, promoted(before))
                self.assert_refused(result, KEPT + "\n")
                self.assertIn("changes newTag and digest in the same commit", result.stderr)
                self.assertEqual(len(result.stderr.splitlines()), 3)

    def test_tag_digest_or_suffix_that_is_not_one_is_refused(self):
        before = kustomization(API_PINNED)

        def entry(fields):
            return kustomization(f"  - name: {API}\n" + "".join(f"    {field}: {value}\n" for field, value in fields))

        def not_one(field, what, value):
            return f"the {field} of its images entry for {API} is not {what}: '{value}'"

        # Kustomize renders API:NEW@DIGEST for this one too, with no digest key to compare.
        smuggled = f"{NEW}@{DIGEST}"
        self.assert_refused(self.step(before, entry([("newTag", smuggled)])),
                            f"{PATH}: {not_one('newTag', 'a tag', smuggled)}\n")
        self.assert_passes(self.step(before, entry([("newTag", NEW)])))
        values = {
            "newTag": ("a tag", ['""', f"{NEW}:{OLD}", f"{NEW}@{DIGEST[:20]}", f".{NEW}", f"-{NEW}", "a" * 129,
                                 f"'{NEW} '", f"{NEW}/{OLD}"]),
            "digest": ("a sha256 digest", ['""', DIGEST.upper().replace("SHA256", "sha256"), DIGEST[:-1], DIGEST + "0",
                                           DIGEST.replace("sha256", "sha512"), DIGEST[7:], f"{DIGEST}:{NEW}",
                                           f"{NEW}@{DIGEST}"]),
            "tagSuffix": ("the end of a tag", ['""', f"'@{DIGEST}'", f"-slim@{DIGEST}", "-slim:x"]),
        }
        for field, (what, written) in values.items():
            for value in written:
                with self.subTest(field=field, value=value):
                    other = [("newTag", NEW)] if field != "newTag" else [("digest", NEW_DIGEST)]
                    head = entry([(field, value)] + other)
                    reason = not_one(field, what, value.strip("'\""))
                    self.assert_refused(self.step(before, head), f"{PATH}: {reason}\n")
                    self.assert_refused(self.step(None, head), reason)
                    # As a base, it does not say what ran.
                    self.assert_refused(self.step(head, promoted(before)), UNKNOWN_BASE + reason + "\n")
        longest = entry([("newTag", "a" * 128), ("digest", NEW_DIGEST), ("tagSuffix", "-slim")])
        self.assert_passes(self.step(before, longest))

    def test_value_that_yaml_does_not_read_as_a_string_is_refused(self):
        # To kustomize 5.0.4 an unquoted null is no tag or no digest, and the same text in quotes is one.
        def entry(tag, digest=DIGEST):
            return kustomization(f"  - name: {API}\n    newTag: {tag}\n    digest: {digest}\n")

        def not_a_string(field, written):
            return f"the {field} of an images entry is not a YAML string, write it in quotes: '{written}'"

        for written in ("null", "Null", "NULL"):
            with self.subTest(field="newTag", written=written):
                # The text of the tag is the same in both commits. The tag is not.
                self.assert_refused(
                    self.step(entry(written), entry(f'"{written}"')),
                    f"{PATH}: {API}: nothing shows that digest '{DIGEST}' is new beside newTag '{written}': "
                    f"in the base commit, {not_a_string('newTag', written)}\n")
        for written in ("null", "Null", "NULL", "~", ""):
            for field, head in {"newTag": entry(written, NEW_DIGEST), "digest": entry(NEW, written)}.items():
                with self.subTest(field=field, written=written):
                    self.assert_refused(self.step(entry(OLD), head), f"{PATH}: {not_a_string(field, written)}\n")
                    self.assert_refused(self.step(head, entry(NEW)), UNKNOWN_BASE + not_a_string(field, written) + "\n")

        for written in ("yes", "true", "2024", "1.0", "0x1F", "2024-01-01"):
            with self.subTest(field="newTag", written=written):
                self.assert_refused(self.step(entry(OLD), entry(written, NEW_DIGEST)),
                                    f"{PATH}: {not_a_string('newTag', written)}\n")
                self.assert_passes(self.step(entry(OLD), entry(f"'{written}'", NEW_DIGEST)))
                self.assert_refused(self.step(entry(OLD), entry(f"'{written}'")),
                                    f"{PATH}: {API}: newTag went from '{OLD}' to '{written}' and digest stayed")
        for field, images in {"name": f"  - name: null\n    newTag: {OLD}\n",
                              "newName": f"  - name: {API}\n    newName: 1.0\n",
                              "tagSuffix": f"  - name: {API}\n    tagSuffix: 2\n"}.items():
            with self.subTest(field=field):
                self.assert_refused(self.step(None, kustomization(images)),
                                    f"{PATH}: the {field} of an images entry is not a YAML string")

    def test_tag_and_digest_that_changed_together_pass(self):
        before = kustomization(API_PINNED)
        self.assert_passes(self.step(before, promoted(before, digest=NEW_DIGEST)))

    def test_entry_that_gains_loses_or_never_had_a_digest_passes(self):
        tag_only, pinned = kustomization(API_TAG_ONLY), kustomization(API_PINNED)
        steps = {
            "no digest, new tag": (tag_only, promoted(tag_only)),
            "gains a digest with the new tag": (tag_only, promoted(pinned)),
            "gains a digest under the same tag": (tag_only, pinned),
            "loses its digest with the new tag": (pinned, promoted(tag_only)),
            "loses its digest under the same tag": (pinned, tag_only),
            "new digest under the same tag": (pinned, pinned.replace(DIGEST, NEW_DIGEST)),
            "loses its tag and keeps the digest": (pinned, pinned.replace(f"    newTag: {OLD}\n", "")),
            "keeps both while a resource changes": (pinned, pinned.replace("deploy.yml", "other.yml")),
            "keeps both in another notation": (pinned, pinned.replace(f"newTag: {OLD}", f'newTag: "{OLD}"')),
        }
        for case, (before, after) in steps.items():
            with self.subTest(case=case):
                self.assertNotEqual(before, after)
                self.assert_passes(self.step(before, after))

    def test_new_tag_added_to_a_digest_only_entry_is_refused(self):
        # What `yq '(.images[] | select(.name == "...") | .newTag) = "..."'` does to such an entry.
        before = kustomization(f"  - name: {API}\n    digest: {DIGEST}\n")
        after = kustomization(f"  - name: {API}\n    digest: {DIGEST}\n    newTag: {NEW}\n")
        self.assert_refused(self.step(before, after),
                            f"{PATH}: {API}: newTag went from none to '{NEW}' and digest stayed '{DIGEST}'")

    def test_only_the_entry_that_kept_its_digest_is_named(self):
        before = kustomization(API_PINNED + WEB_PINNED)
        both = before.replace(OLD, NEW)
        steps = {
            "the first of two": (both.replace(WEB_DIGEST, NEW_DIGEST), [API]),
            "the second of two": (both.replace(DIGEST, NEW_DIGEST), [WEB]),
            "both": (both, [API, WEB]),
        }
        for case, (after, kept) in steps.items():
            with self.subTest(case=case):
                result = self.step(before, after)
                self.assert_refused(result, *(f"{PATH}: {image}: newTag went from" for image in kept))
                self.assertEqual(result.stderr.count("newTag went from"), len(kept))

        self.assert_passes(self.step(before, both.replace(DIGEST, NEW_DIGEST).replace(WEB_DIGEST, NEW_DIGEST)))
        self.assert_passes(self.step(before, promoted(before, digest=NEW_DIGEST)))

    def test_every_changed_kustomization_is_judged_and_no_other_file(self):
        pinned, unreadable = kustomization(API_PINNED), kustomization(ALIASED)
        base = {
            "apps/prod/one/kustomization.yml": pinned,
            "apps/prod/two/kustomization.yml": pinned,
            "apps/prod/untouched/kustomization.yml": unreadable,
            "apps/prod/gone/kustomization.yml": pinned,
            "apps/prod/one/deploy.yml": pinned,
            "apps/prod/one/kustomization.yml.bak": pinned,
            "apps/prod/one/KUSTOMIZATION.YML": pinned,
        }
        head = {path: promoted(text) for path, text in base.items() if "/gone/" not in path}
        head["apps/prod/untouched/kustomization.yml"] = unreadable
        result = self.check(base, head)
        self.assert_refused(result, f"apps/prod/one/kustomization.yml: {API}: newTag went from",
                            f"apps/prod/two/kustomization.yml: {API}: newTag went from")
        self.assertEqual(len(result.stderr.splitlines()), 4)

        head["apps/prod/two/kustomization.yml"] = promoted(pinned, digest=NEW_DIGEST)
        result = self.check(base, head)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "ok: apps/prod/two/kustomization.yml\n")
        self.assertEqual(len(result.stderr.splitlines()), 3)

        head["apps/prod/one/kustomization.yml"] = promoted(pinned, digest=NEW_DIGEST)
        self.assert_passes(self.check(base, head), changed=2)
        self.assert_passes(self.check(base, base), changed=0)

    def test_kustomization_the_base_commit_does_not_have_passes(self):
        after = promoted(kustomization(API_PINNED))
        self.assert_passes(self.step(None, after))
        # A directory that moved in the same change has no base version either.
        self.assert_passes(self.check({"apps/prod/before/kustomization.yml": kustomization(API_PINNED)}, {PATH: after}))

    def test_file_renamed_inside_its_directory_is_compared_with_the_one_it_replaces(self):
        before = kustomization(API_PINNED)
        for name in KUSTOMIZATION_NAMES:
            for other in KUSTOMIZATION_NAMES:
                with self.subTest(base=name, head=other):
                    base, head = {f"apps/prod/demo/{name}": before}, f"apps/prod/demo/{other}"
                    self.assert_refused(self.check(base, {head: promoted(before)}), f"{head}: {API}: newTag went from")
                    self.assert_passes(self.check(base, {head: promoted(before, digest=NEW_DIGEST)}))

    def test_head_that_is_not_one_regular_file_is_refused(self):
        before = kustomization(API_PINNED)
        after = promoted(before, digest=NEW_DIGEST)
        heads = {
            "two kustomization files": (
                {PATH: after, "apps/prod/demo/kustomization.yaml": after},
                "apps/prod/demo: the directory has 2 kustomization files: kustomization.yaml, kustomization.yml"),
            "a symbolic link": ({PATH: ("120000", "real.yml")}, link_at(PATH)[:-1]),
            "a submodule": ({PATH: ("160000", "")}, link_at(PATH, "160000")[:-1]),
        }
        for case, (head, reason) in heads.items():
            for base in ({PATH: before}, {}):
                with self.subTest(case=case, new=not base):
                    result = self.check(base, head)
                    self.assert_refused(result, reason + "\n")
                    self.assertEqual(len(result.stderr.splitlines()), 3)
        self.assert_passes(self.check({PATH: before}, {PATH: ("100755", after)}))

    def test_link_or_submodule_under_apps_is_refused_whatever_changed(self):
        # Kustomize reads real.yml through the link, and no kustomization file of the two commits differs.
        link = {PATH: ("120000", "real.yml")}
        before, other = kustomization(API_PINNED), "apps/prod/other/kustomization.yml"
        base = {**link, "apps/prod/demo/real.yml": before, other: before}
        result = self.check(base, {**base, "apps/prod/demo/real.yml": promoted(before)})
        self.assert_refused(result, link_at(PATH))
        self.assertEqual(len(result.stderr.splitlines()), 3)
        self.assert_refused(self.check(base, base), link_at(PATH))
        # Any change finds the link, and says so beside its own verdict.
        result = self.check(base, {**base, other: promoted(before, digest=NEW_DIGEST)})
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(link_at(PATH), result.stderr)
        self.assertEqual(result.stdout, f"ok: {other}\n")
        # So does a link that is no kustomization file: a directory may be behind it, or nothing this check sees.
        for path, mode in {"apps/prod/demo-link": "120000", "apps/prod/demo/README.md": "120000",
                           "apps/vendor": "160000"}.items():
            with self.subTest(path=path):
                elsewhere = {path: (mode, "../shared"), PATH: before}
                result = self.check(elsewhere, {**elsewhere, PATH: promoted(before, digest=NEW_DIGEST)})
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(result.stderr.splitlines()[0] + "\n", link_at(path, mode))
                self.assertEqual(len(result.stderr.splitlines()), 3)
                self.assertEqual(result.stdout, f"ok: {PATH}\n")
                # A link that only the base commit has is gone.
                self.assert_passes(self.check(elsewhere, {PATH: promoted(before, digest=NEW_DIGEST)}))

    def test_directory_that_was_or_becomes_a_link_or_a_submodule_is_refused(self):
        before = kustomization(API_PINNED)
        elsewhere = {"apps/prod/demo-2/kustomization.yml": promoted(before)}
        links = {
            "a symbolic link": ({"apps/prod/demo": ("120000", "demo-2")}, "apps/prod/demo", "120000"),
            "a submodule": ({"apps/prod/demo": ("160000", "")}, "apps/prod/demo", "160000"),
            "a symbolic link above it": ({"apps/prod": ("120000", "prod-2")}, "apps/prod", "120000"),
        }
        for case, (link, where, mode) in links.items():
            with self.subTest(case=case):
                # The copy the link may lead to is a kustomization the base commit does not have.
                result = self.check({PATH: before}, {**link, **elsewhere})
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(link_at(where, mode), result.stderr)
                self.assertEqual(len(result.stderr.splitlines()), 3)
                self.assertEqual(result.stdout, "ok: apps/prod/demo-2/kustomization.yml\n")
                # The other way round, the directory is new and what the link led to is not read either.
                result = self.check({**link, **elsewhere}, {PATH: promoted(before)})
                self.assert_refused(result, f"{PATH}: {API}: nothing shows that digest '{DIGEST}' is new beside newTag "
                                            f"'{NEW}': in the base commit, a symbolic link or a submodule at {where} "
                                            f"(git mode {mode}) is where this directory is, and what that led to is "
                                            "not read\n")
                self.assertEqual(len(result.stderr.splitlines()), 3)
                self.assert_passes(self.check({**link, **elsewhere}, {PATH: kustomization(API_TAG_ONLY)}))
        # A link that the base commit had beside the directory led somewhere else.
        self.assert_passes(self.check({"apps/prod/demo-link": ("120000", "demo")}, {PATH: promoted(before)}))

    def test_digest_a_line_reader_does_not_see_is_refused(self):
        by_order = {
            "second entry, keys reversed", "second entry, flow style",
            "second entry, a dot where the name has a hyphen",
            "second entry, a dot where the reference has its colon",
            "second entry for the new name of the first",
        }
        no_new_tag_beside_a_digest = {
            # No images list in either commit: kustomize sets no tag and no digest.
            "images block inside a quoted scalar",
            # The entry with the digest applies to another image, whose container keeps
            # API@digest without a tag. The container of API itself gets the new tag.
            "second entry renaming another image to this one",
        }
        fixtures = promotion.unseen_digests()
        self.assertLessEqual(by_order | no_new_tag_beside_a_digest, fixtures.keys())
        for case, (before, reason) in fixtures.items():
            after = promoted(before)
            self.assertNotEqual(after, before)
            with self.subTest(case=case):
                if case in no_new_tag_beside_a_digest:
                    self.assert_passes(self.step(before, after))
                    continue
                reason = SAME_IMAGE if case in by_order else reason
                self.assert_refused(self.step(before, after), f"{PATH}: ", reason)
                # The same file is refused when it is new, and when only a comment changed.
                self.assert_refused(self.step(None, after), reason)
                self.assert_refused(self.step(after, after + "# touched\n"), reason)

    def test_kustomization_the_check_cannot_read_is_refused(self):
        pinned = kustomization(API_PINNED)
        heads = {
            "unknown key in the entry": (
                kustomization(f"{API_PINNED}    extra:\n      - name: nested\n"),
                "an images entry has the key 'extra'"),
            "newTag twice": (
                kustomization(f"{API_PINNED}    newTag: {NEW}\n"), "an images entry has the key 'newTag' twice"),
            "entry without a name": (kustomization(f"{API_PINNED}  - newTag: {NEW}\n"), "an images entry has no name"),
            "entry that is not a mapping": (
                kustomization(f"{API_PINNED}  - {WEB}\n"), "an images entry is not a mapping"),
            "images that is not a list": (pinned.replace("images:\n", "images: !!omap\n"), "images is not a list"),
            "images key in another letter case only": (
                pinned.replace("images:\n", "Images:\n"), "it has no top-level key spelled images"),
            "images key with a long s only": (
                pinned.replace("images:\n", "image\u017f:\n"), "it has no top-level key spelled images"),
            "deprecated imageTags only": (
                pinned.replace("images:\n", "imageTags:\n"), "it uses the deprecated imageTags list"),
            "deprecated imageTags with a long s": (
                kustomization(API_TAG_ONLY) + f"imageTag\u017f:\n{API_PINNED}",
                "it uses the deprecated imageTags list"),
            "digest inside newName": (
                kustomization(f"  - name: {API}\n    newName: {MIRROR}@{DIGEST}\n    newTag: {NEW}\n"),
                "the newName of an images entry is not a plain image name"),
            "two documents": (pinned + "---\nkind: Kustomization\n",
                              "it cannot be read as one YAML document (ComposerError)"),
            "tab in the indentation": (pinned + "patches:\n\t- path: patch.yml\n",
                                       "it cannot be read as one YAML document (ScannerError)"),
            "bytes that are not UTF-8 text": (pinned.encode("utf-8") + b"# \xff\n",
                                              "it cannot be read as one YAML document (UnicodeDecodeError)"),
            "more than this check reads": (
                pinned + "#" * (1 << 20),
                f"it has {len(pinned) + (1 << 20)} bytes, and this check reads at most 1048576"),
        }
        for case, (after, reason) in heads.items():
            with self.subTest(case=case):
                self.assert_refused(self.step(pinned, after), f"{PATH}: {reason}")

    def test_kustomization_without_an_images_list_passes(self):
        bare = promotion.HEAD + promotion.TAIL
        steps = {
            "none before and none after": (bare, bare + "namePrefix: demo-\n"),
            "an alias elsewhere": (bare, bare + "commonLabels: &labels\n  app: demo\ncommonAnnotations: *labels\n"),
            "the list is removed": (kustomization(API_PINNED), bare),
            "the list is added": (bare, kustomization(API_PINNED)),
            "an empty list": (kustomization(API_PINNED), bare + "images: []\n"),
        }
        for case, (before, after) in steps.items():
            with self.subTest(case=case):
                self.assert_passes(self.step(before, after))

    def test_entries_kustomize_could_apply_to_one_image_are_refused_beside_a_digest(self):
        dotted_tag_only = f"  - name: {DOTTED}\n    newTag: {OLD}\n"
        dotted_pinned = f"{dotted_tag_only}    digest: {NEW_DIGEST}\n"
        web_as_api = f"  - name: {WEB}\n    newName: {API}\n    newTag: {OLD}\n"

        def mirrored(new_name, later):
            return (f"  - name: {API}\n    newName: {new_name}\n    newTag: {OLD}\n    digest: {DIGEST}\n"
                    f"  - name: {later}\n    newTag: {NEW}\n")

        refused = {
            "pinned, then tag-only under a name that matches it": API_PINNED + dotted_tag_only,
            "tag-only, then pinned under a name that matches it": API_TAG_ONLY + dotted_pinned,
            "both pinned": API_PINNED + dotted_pinned,
            "pinned under a name with a dot, then tag-only under a name it matches": dotted_pinned + API_TAG_ONLY,
            "the same name twice": API_PINNED + API_TAG_ONLY,
            # A manifest that says API:OLD gets the tag of the first and then the tag and the digest of the second.
            "a name that ends in a dot and a tag, then the pinned entry for the name before the dot": (
                f"  - name: {API}.{OLD}\n    newTag: {NEW}\n" + API_PINNED),
            "pinned, then its name, a dot and its tag as a name": (
                API_PINNED + f"  - name: {API}.{OLD}\n    newTag: {NEW}\n"),
            "pinned, then its name, its tag and its digest with dots between": (
                API_PINNED + f"  - name: {API}.{OLD}.{DIGEST.replace(':', '.')}\n    newTag: {NEW}\n"),
            "tag-only, then its name, a dot and its tag as the name of a pinned one": (
                API_TAG_ONLY + f"  - name: {API}.{OLD}\n    digest: {DIGEST}\n"),
            "renamed to an image that a later entry pins": (
                f"  - name: {API}\n    newName: {WEB}\n    newTag: {OLD}\n" + WEB_PINNED),
            "renamed to an image that a later entry pins under a pattern": (
                f"  - name: {WEB}\n    newName: {API}\n    newTag: {OLD}\n" + dotted_pinned),
            "renamed only, then a pinned entry whose name goes on with a dot": (
                f"  - name: {WEB}\n    newName: {API}\n"
                f"  - name: {API}.{OLD}\n    newTag: {NEW}\n    digest: {DIGEST}\n"),
            "another image renamed to it, then the pinned entry": web_as_api + API_PINNED,
            "pinned and renamed, then an entry for the new name": mirrored(MIRROR, MIRROR),
            "pinned and renamed, then a dot where the new name has a hyphen": mirrored(API + "-2", API + ".2"),
            "pinned and renamed, then the new name, a dot and the tag": mirrored(MIRROR, f"{MIRROR}.{OLD}"),
            "two renames, then the pinned entry": (
                f"  - name: {API}\n    newName: {WEB}\n  - name: {WEB}\n    newName: {MIRROR}\n"
                f"  - name: {MIRROR}\n    newTag: {OLD}\n    digest: {DIGEST}\n"),
            # The first two do not meet each other. The third applies to what either of them left.
            "pinned and renamed, another image renamed to the same name, then an entry for that name": (
                f"  - name: {API}\n    newName: {MIRROR}\n    newTag: {OLD}\n    digest: {DIGEST}\n"
                f"  - name: {WEB}\n    newName: {MIRROR}\n  - name: {MIRROR}\n    newTag: {NEW}\n"),
        }
        for case, images in refused.items():
            with self.subTest(case=case):
                after = kustomization(images)
                for before in (None, after.replace("deploy.yml", "other.yml"), kustomization(API_TAG_ONLY)):
                    result = self.step(before, after)
                    self.assert_refused(result, f"{PATH}: its images entries for ", SAME_IMAGE)
                    self.assertEqual(result.stderr.count(SAME_IMAGE), 1)
        result = self.step(None, kustomization(refused["two renames, then the pinned entry"] + WEB_TAG_ONLY))
        self.assert_refused(result, f"{PATH}: its images entries for {API}, {WEB}, {MIRROR}, {WEB} can apply")

        # Kustomize 5.0.4 applies one entry of each of these to an image, and not the other.
        passes = {
            # The later name asks for a tag that the pinned entry does not leave.
            "pinned, then its name, a dot and another tag as a name": (
                API_PINNED + f"  - name: {API}.sha-sentinel\n    newTag: {NEW}\n"),
            "pinned and renamed, then the new name, a dot and another tag": mirrored(MIRROR, f"{MIRROR}.sha-sentinel"),
            "the pinned entry, then another image renamed to it": API_PINNED + web_as_api,
            "two images renamed to one, one of them pinned": (
                f"  - name: {API}\n    newName: {MIRROR}\n    newTag: {OLD}\n    digest: {DIGEST}\n"
                f"  - name: {WEB}\n    newName: {MIRROR}\n    newTag: {OLD}\n"),
            # The later entry finds no reference left under the old name.
            "pinned and renamed, then an entry for the old name": mirrored(MIRROR, API),
            # The new name is a name, not a pattern: its dot is a dot.
            "pinned and renamed, then a hyphen where the new name has a dot": mirrored(API + ".2", API + "-2"),
            "pinned and renamed, then a longer name": mirrored(MIRROR, MIRROR + "-web"),
            "pinned and renamed to a longer name, then the shorter one": mirrored(MIRROR + ".web", MIRROR),
            "two tag-only entries under names that match": API_TAG_ONLY + dotted_tag_only,
            "two renames, then a tag-only entry": (
                f"  - name: {API}\n    newName: {WEB}\n  - name: {WEB}\n    newName: {MIRROR}\n"
                f"  - name: {MIRROR}\n    newTag: {OLD}\n"),
            "pinned entries for images that only share a prefix": (
                API_PINNED + WEB_PINNED
                + f"  - name: {API[:-1]}\n    digest: {DIGEST}\n"
                + f"  - name: {API}/sidecar\n    digest: {DIGEST}\n"
                + "  - name: clickhouse/clickhouse-server\n    newTag: 24.12-alpine\n"),
        }
        for case, images in passes.items():
            with self.subTest(case=case):
                before = kustomization(images)
                self.assert_passes(self.step(before, promoted(before, digest=NEW_DIGEST)))
                self.assert_passes(self.step(None, before))
                if DIGEST in before.split(OLD)[1].split("- name")[0]:
                    self.assert_refused(self.step(before, promoted(before)), KEPT)

    def test_base_with_entries_for_one_image_does_not_say_which_tag_ran_beside_the_digest(self):
        # A base like these can only be on main after a push that this check refused.
        newer = f"  - name: {API}\n    newTag: {NEW}\n    digest: {DIGEST}\n"
        head = kustomization(newer)
        bridge = f"{API}-bridge"
        bases = {
            # Kustomize applies the later entry: these two ran OLD and NEW beside the digest.
            "the tag of the head first, shadowed": newer + API_PINNED,
            "the tag of the head last, in effect": API_PINNED + newer,
            "the digest under a name that matches": API_TAG_ONLY + f"  - name: {DOTTED}\n    digest: {DIGEST}\n",
            "the digest behind a rename": (
                f"  - name: {API}\n    newName: {MIRROR}\n    newTag: {NEW}\n"
                f"  - name: {MIRROR}\n    digest: {DIGEST}\n"),
            # Kustomize 5.0.4 renders MIRROR:OLD@DIGEST for a manifest that names API.
            "the tag and the digest behind two renames": (
                f"  - name: {API}\n    newName: {bridge}\n  - name: {bridge}\n    newName: {MIRROR}\n"
                f"  - name: {MIRROR}\n    newTag: {OLD}\n    digest: {DIGEST}\n"),
            "the tag and the digest behind three renames": (
                f"  - name: {API}\n    newName: {bridge}\n  - name: {bridge}\n    newName: {bridge}-2\n"
                f"  - name: {bridge}-2\n    newName: {MIRROR}\n"
                f"  - name: {MIRROR}\n    newTag: {OLD}\n    digest: {DIGEST}\n"),
        }
        for case, images in bases.items():
            for renamed in ("", f"    newName: {MIRROR}\n"):
                with self.subTest(case=case, head_renames=bool(renamed)):
                    before = kustomization(images + WEB_PINNED)
                    after = head.replace(f"    newTag: {NEW}\n", f"{renamed}    newTag: {NEW}\n")
                    result = self.step(before, after)
                    self.assert_refused(result, UNDECIDED_BASE)
                    self.assertEqual(len(result.stderr.splitlines()), 3)
                    # A digest the base does not have anywhere is new, whatever ran before.
                    self.assert_passes(self.step(before, after.replace(DIGEST, NEW_DIGEST)))
                    # Another image of the same kustomization is judged by its own entry.
                    repaired = kustomization(newer.replace(DIGEST, NEW_DIGEST) + WEB_PINNED)
                    self.assert_passes(self.step(before, repaired.replace("deploy.yml", "other.yml")))
                    tag_only = repaired.replace(f"{WEB}\n    newTag: {OLD}", f"{WEB}\n    newTag: {NEW}")
                    result = self.step(before, tag_only)
                    self.assert_refused(result, f"{PATH}: {WEB}: newTag went from '{OLD}' to '{NEW}' and digest stayed")
                    self.assertNotIn("nothing shows", result.stderr)

    def test_head_entry_that_matches_several_base_entries_answers_for_each(self):
        # Two images, each with its entry, and then one entry whose name matches both.
        underscored = API.replace("rbx-", "rbx_")
        before = kustomization(API_PINNED + f"  - name: {underscored}\n    newTag: {NEW}\n    digest: {DIGEST}\n")
        after = kustomization(f"  - name: {DOTTED}\n    newTag: {NEW}\n    digest: {DIGEST}\n")
        self.assert_refused(self.step(before, after),
                            f"{PATH}: {DOTTED}: newTag went from '{OLD}' to '{NEW}' and digest stayed '{DIGEST}'")
        self.assert_passes(self.step(before.replace(OLD, NEW), after))
        self.assert_passes(self.step(before, after.replace(DIGEST, NEW_DIGEST)))

    def test_entry_whose_name_is_respelled_is_still_the_entry_for_that_image(self):
        before = kustomization(f"  - name: {DOTTED}\n    newTag: {OLD}\n    digest: {DIGEST}\n")
        after = kustomization(API_PINNED)
        self.assert_passes(self.step(before, after))
        self.assert_refused(self.step(before, promoted(after)), KEPT)
        self.assert_passes(self.step(before, promoted(after, digest=NEW_DIGEST)))
        # Another image that happens to carry the same digest is not that entry.
        self.assert_passes(self.step(kustomization(WEB_TAG_ONLY + f"    digest: {DIGEST}\n"), promoted(after)))

    def test_renamed_image_is_judged_like_any_other(self):
        # apps/staging/kustomization.yml renames an image to its own name.
        same = f"  - name: {API}\n    newName: {API}\n    newTag: {OLD}\n"
        mirrored = f"  - name: {API}\n    newName: {MIRROR}\n    newTag: {OLD}\n    digest: {DIGEST}\n"
        for case, images in {"to its own name": same, "to another name, pinned": mirrored}.items():
            with self.subTest(case=case):
                before = kustomization(images + WEB_TAG_ONLY)
                if DIGEST in images:
                    self.assert_refused(self.step(before, promoted(before)), KEPT)
                    self.assert_refused(self.step(before, promoted(before).replace(MIRROR, f"{MIRROR}-2")), KEPT)
                else:
                    self.assert_passes(self.step(before, promoted(before)))
                self.assert_passes(self.step(before, promoted(before, digest=NEW_DIGEST)))

    def test_base_the_check_cannot_read_lets_no_tag_through_beside_a_digest(self):
        after = promoted(kustomization(API_PINNED + WEB_TAG_ONLY))
        bases = {
            "an alias": ({PATH: kustomization(ALIASED)}, "it uses a YAML alias"),
            "two kustomization files": ({PATH: after, "apps/prod/demo/Kustomization": after},
                                        "the directory has 2 kustomization files: Kustomization, kustomization.yml"),
            "a symbolic link": ({PATH: ("120000", "../other/kustomization.yml")},
                                "it is not a regular file (git mode 120000)" + NO_LINK),
            "a tag that carries a digest": (
                {PATH: kustomization(f"  - name: {API}\n    newTag: {OLD}@{DIGEST}\n")},
                f"the newTag of its images entry for {API} is not a tag: '{OLD}@{DIGEST}'"),
        }
        for case, (base, reason) in bases.items():
            with self.subTest(case=case):
                result = self.check(base, {PATH: after})
                self.assert_refused(result, UNKNOWN_BASE + reason + "\n")
                self.assertEqual(len(result.stderr.splitlines()), 3)
                # Changed digest or not, the base does not say what it replaces.
                self.assert_refused(self.check(base, {PATH: after.replace(DIGEST, NEW_DIGEST)}), reason)
                for unpinned in (kustomization(API_TAG_ONLY), kustomization(f"  - name: {API}\n    digest: {DIGEST}\n"),
                                 promotion.HEAD + promotion.TAIL):
                    self.assert_passes(self.check(base, {PATH: unpinned}))

    def test_no_change_that_a_model_of_kustomize_finds_at_fault_passes(self):
        # The check runs in this process here, a few thousand times.
        spec = importlib.util.spec_from_file_location("check_digest_pin_promotion", CHECK)
        check = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(check)

        def written(entries):
            lines = "".join("  - " + "\n    ".join(f'{field}: "{value}"' for field, value in entry.items()) + "\n"
                            for entry in entries)
            return promotion.HEAD + (f"images:\n{lines}" if entries else "")

        # The lists are drawn from a fixed seed, to be the same in every run. Nothing is kept secret with them.
        rng = random.Random(20261114)  # nosec B311
        passed, refused, missed = 0, {}, []
        for _ in range(6000):
            base = [model_entry(rng) for _ in range(rng.randint(0, 3))]
            head = [model_entry(rng) for _ in range(rng.randint(1, 3))]
            kind = rng.random()
            if kind < 0.1:
                base, head = model_chain(rng)
            elif kind < 0.7:
                head = model_change(rng, base)
            if written(base) == written(head):
                continue
            self.commit("base", {PATH: written(base)})
            self.commit("head", {PATH: written(head)})
            errors = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(errors):
                status = check.main([str(self.export)])
            self.assertIn(status, (0, 1), errors.getvalue())
            fault = model_fault(base, head)
            if status:
                refused[bool(fault)] = refused.get(bool(fault), 0) + 1
            elif fault:
                missed.append((fault, model.apply(base, fault), model.apply(head, fault), base, head))
            else:
                passed += 1
        self.assertEqual(missed[:3], [])
        # Enough of each kind for the line above to mean something.
        self.assertGreater(refused[True], 400)
        self.assertGreater(passed, 2500)

    def test_path_is_shown_on_one_line(self):
        path = "apps/prod/de\nmo \u00e9/kustomization.yml"
        before = kustomization(API_PINNED)
        result = self.check({path: before}, {path: promoted(before)})
        self.assert_refused(result, f"apps/prod/de\\nmo \\xe9/kustomization.yml: {API}: newTag went from")
        self.assertEqual(len(result.stderr.splitlines()), 3)
        result = self.check({path: before}, {path: promoted(before, digest=NEW_DIGEST)})
        self.assertEqual(result.stdout.splitlines()[0], "ok: apps/prod/de\\nmo \\xe9/kustomization.yml")

    def test_export_the_shell_script_did_not_write_is_an_error(self):
        pinned = kustomization(API_PINNED)

        def run(*arguments):
            return subprocess.run(  # nosec B603
                [sys.executable, "-I", "-B", str(CHECK), *arguments],
                capture_output=True, encoding="utf-8", check=False)

        def broken(case, reason):
            with self.subTest(case=case):
                result = run(str(self.export))
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn(reason, result.stderr)
                self.assertEqual(result.stdout, "")

        self.assertEqual(run().returncode, 2)
        self.assertEqual(run(str(self.export), str(self.export)).returncode, 2)
        broken("no listings", "base.tree")
        self.commit("base", {PATH: pinned})
        broken("no head listing", "head.tree")
        self.commit("head", {PATH: promoted(pinned)})
        for blob in (self.export / "blobs").iterdir():
            blob.unlink()
        broken("no contents", "no contents for object ")
        (self.export / "head.tree").write_bytes(f"100644 blob ../../etc/passwd\t{PATH}\0".encode("utf-8"))
        broken("an object id that is a path", "not a line of git ls-tree")
        (self.export / "head.tree").write_bytes(f"100644 blob {'0' * 40} {PATH}\0".encode("utf-8"))
        broken("no tab before the path", "not a line of git ls-tree")

    def test_every_kustomization_of_the_repository_can_be_judged(self):
        base = {path.relative_to(REPO).as_posix(): path.read_text(encoding="utf-8")
                for name in KUSTOMIZATION_NAMES for path in sorted((REPO / "apps").rglob(name))}
        self.assertGreater(len(base), 40)
        touched = {path: text + "# touched\n" for path, text in base.items()}
        self.assert_passes(self.check(base, touched), changed=len(base))

        pinned = 0
        for path, text in base.items():
            for entry in yaml.safe_load(text).get("images") or []:
                if "digest" not in entry:
                    continue
                with self.subTest(path=path, image=entry["name"]):
                    stanza = f"    newTag: {entry['newTag']}\n    digest: {entry['digest']}\n"
                    self.assertEqual(text.count(stanza), 1)
                    tag_only = text.replace(stanza, f"    newTag: {NEW}\n    digest: {entry['digest']}\n")
                    result = self.check(base, {**base, path: tag_only})
                    self.assert_refused(result, f"{path}: {entry['name']}: newTag went from '{entry['newTag']}' "
                                                f"to '{NEW}' and digest stayed '{entry['digest']}'")
                    self.assertEqual(len(result.stderr.splitlines()), 3)
                    together = text.replace(stanza, f"    newTag: {NEW}\n    digest: {NEW_DIGEST}\n")
                    self.assert_passes(self.check(base, {**base, path: together}))
                    pinned += 1
        self.assertGreater(pinned, 0)


if __name__ == "__main__":
    unittest.main()
