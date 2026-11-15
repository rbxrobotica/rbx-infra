"""Throwaway git repositories; the check as validate.yml runs it.

scripts/check-digest-pin-promotion.sh runs git here and reads the two commits
in podman, in the pinned image and the environment of
scripts/promote-image-tag-env.sh. These tests therefore need git, podman and
the network that provisioning needs. scripts/test-promote-image-tag.sh runs
them on the machine itself, with its python3 and the standard library.
"""

import os
from pathlib import Path
# The tests run git and the script under test as processes.
import subprocess  # nosec B404
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "check-digest-pin-promotion.sh"

API = "ghcr.io/rbxrobotica/rbx-commerce"
OLD = "sha-1111111"
NEW = "sha-2222222"
DIGEST = "sha256:" + "a" * 64
NEW_DIGEST = "sha256:" + "b" * 64
DEMO = "apps/prod/demo/kustomization.yml"
OTHER = "apps/prod/other/kustomization.yml"

PINNED = (
    "apiVersion: kustomize.config.k8s.io/v1beta1\n"
    "kind: Kustomization\n"
    "resources:\n"
    "  - deploy.yml\n"
    "images:\n"
    f"  - name: {API}\n    newTag: {OLD}\n    digest: {DIGEST}\n"
)
TAG_ONLY = PINNED.replace(OLD, NEW)
TOGETHER = TAG_ONLY.replace(DIGEST, NEW_DIGEST)
KEPT = f"{API}: newTag went from '{OLD}' to '{NEW}' and digest stayed '{DIGEST}'"
NOTHING = "nothing under apps/ differs between the two commits\n"


class CheckDigestPinPromotionGitTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        # Nothing of this machine's git configuration reaches the fixtures.
        self.environment = {
            **os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.invalid",
        }
        self.git("init", "--quiet", "--initial-branch=main")
        self.commit({DEMO: PINNED, OTHER: PINNED, "README.md": "fixture\n"})

    def git(self, *arguments, cwd=None):
        # The argv is fixed by the tests and git comes from PATH, as in validate.yml.
        result = subprocess.run(  # nosec B603 B607
            ["git", *arguments], cwd=cwd or self.repo, env=self.environment,
            capture_output=True, encoding="utf-8", check=True)
        return result.stdout.strip()

    def commit(self, files):
        """Commit files, path -> contents, and return the commit."""
        for path, contents in files.items():
            target = self.repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(contents, encoding="utf-8")
        self.git("add", "--all")
        self.git("commit", "--quiet", "--message", "fixture")
        return self.git("rev-parse", "HEAD")

    def check(self, *arguments, cwd=None, **extra):
        # The argv is fixed by the tests and bash comes from PATH, as in validate.yml.
        return subprocess.run(  # nosec B603 B607
            ["bash", str(SCRIPT), *arguments], cwd=cwd or self.repo, env={**self.environment, **extra},
            capture_output=True, encoding="utf-8", check=False)

    def assert_passes(self, result, *lines):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        for line in lines:
            self.assertIn(line + "\n", result.stdout)

    def assert_refused(self, result, *reasons):
        self.assertEqual(result.returncode, 1, result.stderr)
        for reason in reasons:
            self.assertIn(reason + "\n", result.stderr)
        self.assertNotIn("ok: ", result.stdout)

    def assert_refused_beside(self, result, reason, passed):
        """One finding, and a kustomization that was compared and passed."""
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stderr.splitlines()[0], reason)
        self.assertEqual(len(result.stderr.splitlines()), 3)
        self.assertIn(f"ok: {passed}\n", result.stdout)

    def test_push_is_judged_from_the_commit_before_it_to_its_head(self):
        base = self.git("rev-parse", "HEAD")
        tag_only = self.commit({DEMO: TAG_ONLY})
        together = self.commit({DEMO: TOGETHER})
        result = self.check(f"{base}..{tag_only}")
        self.assert_refused(result, f"{DEMO}: {KEPT}")
        self.assertIn(f"comparing {base} (base) with {tag_only} (head)\n", result.stdout)
        self.assertIn("PyYAML 6.0.3\n", result.stdout)
        # The check reads commits, not the files that happen to be checked out,
        # and names them from the top of the repository wherever it is started.
        self.assert_refused(self.check("HEAD~2..HEAD~1"), f"{DEMO}: {KEPT}")
        self.assert_refused(self.check("HEAD~2..HEAD~1", cwd=self.repo / "apps" / "prod" / "other"), f"{DEMO}: {KEPT}")
        self.assert_passes(self.check("HEAD~1..HEAD"), f"ok: {DEMO}")
        self.assert_passes(self.check(f"{base}..{together}"), f"ok: {DEMO}",
                           "1 changed kustomization file(s), none with a new tag beside an old digest")

    def test_pull_request_is_judged_from_where_it_left_its_base_branch(self):
        self.git("checkout", "--quiet", "-b", "together")
        self.commit({DEMO: TOGETHER})
        self.git("checkout", "--quiet", "-b", "tag-only", "main")
        self.commit({DEMO: TAG_ONLY})
        self.git("checkout", "--quiet", "main")
        self.commit({OTHER: PINNED.replace(f"    newTag: {OLD}\n", "")})
        self.assert_passes(self.check("main...together"), f"ok: {DEMO}",
                           "1 changed kustomization file(s), none with a new tag beside an old digest")
        self.assert_refused(self.check("main...tag-only"), f"{DEMO}: {KEPT}")
        # Against main itself, the branch would answer for undoing what main did since.
        result = self.check("main..together")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(f"{OTHER}: {API}: newTag went from none to '{OLD}' and digest stayed '{DIGEST}'\n", result.stderr)
        self.assertIn(f"ok: {DEMO}\n", result.stdout)

    def test_pull_request_merge_is_judged_against_the_base_branch_it_merges_into(self):
        # validate.yml checks out the merge commit GitHub makes for a pull request.
        late = "apps/prod/late/kustomization.yml"
        tag_only = PINNED.replace(f"    digest: {DIGEST}\n", "").replace(
            f"    newTag: {OLD}\n", f"    # Written by the pipeline of the application.\n    #\n    newTag: {OLD}\n")
        self.commit({late: tag_only})
        self.git("checkout", "--quiet", "-b", "promotion")
        self.commit({late: tag_only.replace(OLD, NEW)})
        self.git("checkout", "--quiet", "main")
        # Meanwhile main pins the image by digest, in lines the branch did not touch.
        self.commit({late: tag_only.replace(f"  - name: {API}\n", f"  - digest: {DIGEST}\n    name: {API}\n")})
        self.git("checkout", "--quiet", "--detach", "main")
        self.git("merge", "--quiet", "--no-ff", "--message", "merge", "promotion")
        merge = self.git("rev-parse", "HEAD")
        self.assertIn(f"  - digest: {DIGEST}\n", (self.repo / late).read_text(encoding="utf-8"))
        self.assertIn(f"    newTag: {NEW}\n", (self.repo / late).read_text(encoding="utf-8"))
        # And main moves on after GitHub made the merge commit.
        self.git("checkout", "--quiet", "main")
        self.commit({OTHER: TOGETHER})

        result = self.check(f"main...{merge}")
        self.assert_refused(result, f"{late}: {API}: newTag went from '{OLD}' to '{NEW}' and digest stayed '{DIGEST}'")
        self.assertIn(f"comparing {self.git('rev-parse', f'{merge}^1')} (base) with {merge} (head)\n", result.stdout)
        self.assertEqual(len(result.stderr.splitlines()), 3)
        # The branch by itself changed a tag that had no digest beside it.
        self.assert_passes(self.check("main...promotion"), f"ok: {late}")

    def test_change_outside_apps_starts_no_container(self):
        self.commit({"README.md": "changed\n", "docs/apps/kustomization.yml": TAG_ONLY})
        stub = self.root / "bin"
        stub.mkdir()
        (stub / "podman").write_text("#!/bin/sh\necho podman was started >&2\nexit 97\n", encoding="utf-8")
        (stub / "podman").chmod(0o755)
        path = f"{stub}{os.pathsep}{os.environ['PATH']}"
        result = self.check("HEAD~1..HEAD", PATH=path)
        self.assert_passes(result)
        self.assertTrue(result.stdout.endswith(NOTHING), result.stdout)
        self.assert_passes(self.check("HEAD...HEAD", PATH=path))
        # With a change under apps/ the same stub is reached, and its failure is the result.
        self.commit({DEMO: TAG_ONLY})
        result = self.check("HEAD~1..HEAD", PATH=path)
        self.assertEqual(result.returncode, 97)
        self.assertEqual(result.stderr, "podman was started\n")

    def test_range_that_cannot_be_compared_is_an_error(self):
        self.git("checkout", "--quiet", "--orphan", "unrelated")
        self.commit({DEMO: TAG_ONLY})
        self.git("checkout", "--quiet", "main")
        self.git("clone", "--quiet", "--depth", "1", self.repo.as_uri(), str(self.root / "shallow"))
        blob = self.git("rev-parse", "HEAD:README.md")
        ranges = {
            "no range": ([], "usage: "),
            "two arguments": (["HEAD~1", "HEAD"], "usage: "),
            "one commit": (["HEAD"], "usage: "),
            "no base": (["..HEAD"], "usage: "),
            "no head": (["HEAD..."], "usage: "),
            "an option": (["--all..HEAD"], "usage: "),
            "a branch that does not exist": (["missing..HEAD"], "not a commit of this repository: missing"),
            "the commit before a new branch": ([f"{'0' * 40}..HEAD"], f"not a commit of this repository: {'0' * 40}"),
            "an object that is not a commit": ([f"{blob}..HEAD"], f"not a commit of this repository: {blob}"),
            "four dots": (["HEAD....HEAD"], "not a commit of this repository: .HEAD"),
            "branches without a common ancestor": (["main...unrelated"], "main and unrelated have no common ancestor"),
        }
        for case, (arguments, reason) in ranges.items():
            with self.subTest(case=case):
                result = self.check(*arguments)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn(reason, result.stderr)
                self.assertEqual(result.stdout, "")
        result = self.check("HEAD...HEAD", cwd=self.root / "shallow")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("this clone is shallow", result.stderr)
        self.assert_passes(self.check("HEAD..HEAD", cwd=self.root / "shallow"))
        result = self.check("HEAD..HEAD", cwd=self.root)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("run this inside the work tree of the repository to check", result.stderr)

    def test_kustomization_is_found_under_any_of_its_names_and_paths(self):
        self.git("mv", DEMO, "apps/prod/demo/kustomization.yaml")
        self.commit({"apps/prod/demo/kustomization.yaml": TAG_ONLY + "namePrefix: demo-\n"})
        self.assert_refused(self.check("HEAD~1..HEAD"), f"apps/prod/demo/kustomization.yaml: {KEPT}")

        # Contents that no other file of the fixture has: git stores equal files once.
        odd = "apps/prod/a b\tc\nd \u00e9/Kustomization"
        self.commit({odd: PINNED + "namePrefix: odd-\n"})
        self.commit({odd: TAG_ONLY + "namePrefix: odd-\n"})
        self.assert_refused(self.check("HEAD~1..HEAD"), f"apps/prod/a b\\tc\\nd \\xe9/Kustomization: {KEPT}")

    def test_git_attributes_do_not_hide_a_kustomization(self):
        attributes = "apps/** export-ignore -diff -text filter=missing\n"
        self.commit({DEMO: TAG_ONLY, ".gitattributes": attributes, "apps/.gitattributes": attributes})
        self.assert_refused(self.check("HEAD~1..HEAD"), f"{DEMO}: {KEPT}")

    def test_link_or_submodule_under_apps_is_refused_whatever_changed(self):
        def refusal(path, mode):
            return (f"{path}: a symbolic link or a submodule (git mode {mode}): kustomize may read through it, and "
                    "this check does not follow it")

        def changed():
            return self.git("diff", "--name-only", "HEAD~1", "HEAD")

        # The kustomization becomes a link to a file beside it.
        (self.repo / DEMO).unlink()
        (self.repo / DEMO).symlink_to("real.yml")
        self.commit({"apps/prod/demo/real.yml": PINNED})
        self.assert_refused(self.check("HEAD~1..HEAD"), refusal(DEMO, "120000"))
        # With the link in both commits, only the file it leads to changes.
        self.commit({"apps/prod/demo/real.yml": TAG_ONLY})
        self.assertEqual(changed(), "apps/prod/demo/real.yml")
        self.assert_refused(self.check("HEAD~1..HEAD"), refusal(DEMO, "120000"))
        # The link leads out of apps/, and nothing under apps/ changes.
        (self.repo / DEMO).unlink()
        (self.repo / DEMO).symlink_to("../../../shared/real.yml")
        self.commit({"shared/real.yml": PINNED})
        self.commit({"shared/real.yml": TAG_ONLY})
        self.assertEqual(changed(), "shared/real.yml")
        result = self.check("HEAD~1..HEAD")
        self.assert_refused(result, refusal(DEMO, "120000"))
        self.assertEqual(len(result.stderr.splitlines()), 3)
        # A file again: what ran behind the link is not known, so a tag beside a digest is refused once more.
        (self.repo / DEMO).unlink()
        self.commit({DEMO: TOGETHER})
        self.assert_refused(
            self.check("HEAD~1..HEAD"),
            f"{DEMO}: {API}: nothing shows that digest '{NEW_DIGEST}' is new beside newTag '{NEW}': in the base "
            "commit, it is not a regular file (git mode 120000), and this check follows no link")

        # A directory becomes a link to a copy of it with the tag changed alone.
        self.git("mv", "apps/prod/other", "apps/prod/copy")
        (self.repo / "apps/prod/other").symlink_to("copy")
        self.commit({"apps/prod/copy/kustomization.yml": TAG_ONLY})
        result = self.check("HEAD~1..HEAD")
        self.assert_refused_beside(result, refusal("apps/prod/other", "120000"), "apps/prod/copy/kustomization.yml")
        (self.repo / "apps/prod/other").unlink()
        self.commit({})

        # A submodule, and then a change outside apps/.
        (self.repo / "apps/vendor").mkdir()
        self.git("update-index", "--add", "--cacheinfo", f"160000,{self.git('rev-parse', 'HEAD')},apps/vendor")
        self.git("commit", "--quiet", "--message", "fixture")
        self.assert_refused(self.check("HEAD~1..HEAD"), refusal("apps/vendor", "160000"))
        self.commit({"README.md": "changed\n"})
        self.assertEqual(changed(), "README.md")
        result = self.check("HEAD~1..HEAD")
        self.assert_refused(result, refusal("apps/vendor", "160000"))
        self.assertEqual(len(result.stderr.splitlines()), 3)
        # One where a kustomization file would be has no contents to hand to the reader.
        late = "apps/prod/late/kustomization.yml"
        self.git("update-index", "--add", "--cacheinfo", f"160000,{self.git('rev-parse', 'HEAD')},{late}")
        self.git("commit", "--quiet", "--message", "fixture")
        result = self.check("HEAD~1..HEAD")
        self.assert_refused(result, refusal(late, "160000"), refusal("apps/vendor", "160000"))
        self.assertEqual(len(result.stderr.splitlines()), 4)


if __name__ == "__main__":
    unittest.main()
