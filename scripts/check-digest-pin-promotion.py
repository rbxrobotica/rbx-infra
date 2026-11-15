#!/usr/bin/env python3
"""Fail a change that gives a digest-pinned image a new tag beside its old digest.

Usage: check-digest-pin-promotion.py <export>

Kustomize renders an images entry with a tag and a digest as image:tag@digest
and the cluster pulls by digest. A change that moves newTag and leaves digest
as it was shows the new tag while the old image keeps running.
scripts/promote-image-tag.sh refuses to make that edit. A hand edit and the
self-push of the application repositories (docs/infra/IMAGE-PROMOTION.md) do
not go through that script; this check is for them.

scripts/check-digest-pin-promotion.sh writes <export> from two commits, base
and head: base.tree and head.tree, their `git ls-tree -r -z` listings, and
blobs/<object id>, the contents of each kustomization file they list. For
every directory whose kustomization file differs between the two commits, this
reads both versions by the rules of scripts/promote-image-tag-verify.py (its
read(), with newName allowed and every value of an entry a YAML string) and
refuses the change when:

- an entry of the head version has a newTag and a digest, and an entry of the
  base version whose name could match the same image had that digest and
  another newTag, or none. The tag is new and the digest is not. A newTag
  added to an entry that had only the digest counts: that is what the yq line
  of the self-push template does to such an entry;
- the head version cannot be read by those rules, or its directory has two
  kustomization files;
- a newTag of the head version is not a tag, a digest is not a sha256 digest,
  or a tagSuffix is not the end of a tag. `newTag: new@sha256:...` without a
  digest key renders the same pinned reference as the two keys;
- the head version has entries that kustomize could apply to one image, one
  after another, and one of them has a digest. Kustomize applies the entries
  in their order, and a later one applies to what an earlier one left: the
  name it gave with newName, the tag and the digest it set
  (scripts/tests/kustomize_images_model.py). Which tag and digest the image
  gets then depends on that order, and one entry compared with its earlier
  self no longer says what the image ran before. Such entries are refused,
  not worked out;
- the directory, or one above it, was a symbolic link or a submodule in the
  base commit, and the head version has an entry with a newTag and a digest;
- the base version has such a group around an entry for the image of a head
  entry with a newTag and a digest, and has that digest somewhere: the tag it
  ran beside that digest is not decided here;
- the base version cannot be read and the head version has an entry with a
  newTag and a digest: nothing shows that this digest is new.

Whatever changed, it also refuses a head commit that has a symbolic link or a
submodule anywhere under apps/. Kustomize reads through a link and this check
does not, so what a link leads to can change while no kustomization file does.

It passes an entry whose newTag and digest both changed, one that gained or
lost its digest, one that lost its newTag, one without a digest, a
kustomization without an images list, and a kustomization the base commit does
not have in that directory. A value that YAML reads as something other than a
string is refused: `newTag: null` is no tag to kustomize and `newTag: "null"`
is one.

What it does not see:

- whether the digest is the digest of the tag. It compares two commits and
  asks no registry: a tag and a digest that changed together pass, whatever
  they are, and so does a digest that changed alone;
- a build that publishes the same contents under a new tag. It has the old
  digest, and the change is refused although it is right. There is nothing to
  deploy then: keep the old tag;
- a digest that comes from outside the images list of that kustomization (a
  patch, a replacement, a transformer file, a component, a base), or a tag or
  a digest that a manifest carries itself;
- a kustomization outside apps/: the shell script exports that directory only;
- an application directory that was moved or renamed in the same change, as
  files, or an entry whose name changed to one that cannot match what the old
  one matched. Neither has a base version;
- the commits between base and head, and what an earlier change did. An entry
  that drops its digest in one change and gets it back beside a new tag in the
  next passes both.

Nor does it run before a push to main, or at all on a push or a pull request
whose head commit says [skip ci]: see docs/infra/IMAGE-PROMOTION.md.

Exit status: 0 nothing to refuse, 1 at least one finding, on standard error,
2 <export> is not what the shell script writes. Reads <export> and writes
nothing.
"""

import importlib.util
import itertools
from pathlib import Path
import re
import sys

KUSTOMIZATION_NAMES = ("kustomization.yaml", "kustomization.yml", "Kustomization")
OBJECT_ID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
# What a newTag, a digest and a tagSuffix have to be. Kustomize pastes them into
# the reference as they are, so a newTag could carry a digest behind an at sign.
SHAPES = {
    "newTag": (re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}"), "a tag"),
    "digest": (re.compile(r"sha256:[0-9a-f]{64}"), "a sha256 digest"),
    "tagSuffix": (re.compile(r"[A-Za-z0-9_.-]{1,128}"), "the end of a tag"),
}
# What kustomize lets follow the name of an entry in a reference it matches.
TAIL = r"(:[a-zA-Z0-9_.{}-]*)?(@sha256:[a-zA-Z0-9_.{}-]*)?"
# The largest kustomization of this repository is a few kilobytes.
MAX_BYTES = 1 << 20

EXPLANATION = (
    "kustomize renders an images entry with a tag and a digest as image:tag@digest and the cluster pulls by digest, "
    "so a new tag beside the old digest keeps running the old image\n"
    "a promotion of a digest-pinned image changes newTag and digest in the same commit, with the digest that the "
    "build of that tag printed"
)


def load_verifier():
    path = Path(__file__).resolve().with_name("promote-image-tag-verify.py")
    spec = importlib.util.spec_from_file_location("promote_image_tag_verify", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verifier = load_verifier()


class Malformed(Exception):
    pass


def shown(path):
    """A path of the repository as one line of ASCII."""
    return path.encode("unicode_escape").decode("ascii")


def kustomizations(listing):
    """The kustomization files of a `git ls-tree -r -z` listing, by directory, and the git mode of each path in it
    that is a symbolic link or a submodule."""
    found, links = {}, {}
    for record in listing.split(b"\0"):
        if not record:
            continue
        meta, tab, path = record.partition(b"\t")
        fields = meta.decode("ascii", "replace").split(" ")
        if not tab or len(fields) != 3 or not OBJECT_ID.fullmatch(fields[2]):
            raise Malformed(f"not a line of git ls-tree: {record!r}")
        path = path.decode("utf-8", "surrogateescape")
        if not fields[0].startswith("100"):
            links[path] = fields[0]
        directory, _, name = path.rpartition("/")
        if name in KUSTOMIZATION_NAMES:
            found.setdefault(directory, []).append((name, *fields))
    return found, links


def leading_elsewhere(directory, links):
    """The symbolic link or submodule that the base commit has where the head commit has a directory, or above it."""
    parts = directory.split("/")
    for path in ("/".join(parts[:depth]) for depth in range(1, len(parts) + 1)):
        if path in links:
            return f"a symbolic link or a submodule at {shown(path)} (git mode {links[path]})"
    return None


def regular(file):
    """Whether a kustomization file of a listing is a file, not a symbolic link or a submodule."""
    _, mode, kind, _ = file
    return kind == "blob" and mode.startswith("100")


def version(files, blobs):
    """The images entries of the kustomization one commit has in one directory."""
    if len(files) != 1:
        names = ", ".join(sorted(shown(name) for name, *_ in files))
        raise verifier.Refused(f"the directory has {len(files)} kustomization files: {names}")
    _, mode, _, object_id = files[0]
    if not regular(files[0]):
        raise verifier.Refused(f"it is not a regular file (git mode {mode}), and this check follows no link")
    blob = blobs / object_id
    try:
        size = blob.stat().st_size
    except OSError as error:
        raise Malformed(f"no contents for object {object_id}") from error
    if size > MAX_BYTES:
        raise verifier.Refused(f"it has {size} bytes, and this check reads at most {MAX_BYTES}")
    try:
        entries, _ = verifier.read(blob, renames=True, strings=True)
    except verifier.Absent:
        return []
    for entry in entries:
        for field, (shape, what) in SHAPES.items():
            if field in entry and not shape.fullmatch(entry[field]):
                raise verifier.Refused(f"the {field} of its images entry for {entry['name']} is not {what}: "
                                       f"{entry[field]!a}")
    return entries


def outputs(entry):
    """What an entry leaves of the references it applies to: a name, whether that name is a pattern, and what
    follows the name.

    The name is the one newName gives. Without newName it is the name of the
    reference, which the name of the entry matched as a pattern: up to its end,
    or up to one of its dots that stood for the colon or the at sign. What
    follows is the tag and the digest the entry sets, or None when it sets
    neither and the reference keeps its own.
    """
    follows = None
    if "newTag" in entry or "digest" in entry:
        follows = "".join(mark + entry[field] for mark, field in ((":", "newTag"), ("@", "digest")) if field in entry)
    if "newName" in entry:
        return [(entry["newName"], False, follows)]
    name = entry["name"]
    ends = [len(name)] + [index for index, char in enumerate(name) if char == "." and "/" not in name[index:]]
    return [(name[:end], True, follows) for end in ends]


def reaches(pattern, name, wild, follows):
    """Whether the name of an entry, which kustomize reads as a pattern, matches a reference made of a name and of
    what follows it.

    In a plain name only the dot is special. With wild, the name of the
    reference is a pattern too, for any of the names it matches.
    """
    if len(pattern) < len(name) or any(a not in (b, ".") and not (wild and b == ".") for a, b in zip(pattern, name)):
        return False
    beyond = pattern[len(name):]
    if follows is None:
        return not beyond or beyond[0] == "."
    return re.fullmatch(beyond + TAIL, follows) is not None


def groups(entries):
    """The entries of one images list, grouped by the images kustomize could apply them to.

    Kustomize applies the entries in their order, and a later one applies to
    what an earlier one left. Two entries are in one group when the later one
    could apply to something the earlier one left, directly or through others.
    Two entries that rename different images to the same name are not.
    """
    group = list(range(len(entries)))
    for (first, earlier), (second, later) in itertools.combinations(enumerate(entries), 2):
        if any(reaches(later["name"], *output) for output in outputs(earlier)):
            group = [group[first] if mark == group[second] else mark for mark in group]
    return [[entry for entry, mark in zip(entries, group) if mark == wanted] for wanted in sorted(set(group))]


def undecided(entries):
    """The groups of an images list in which the order decides what tag runs beside a digest."""
    return [group for group in groups(entries) if len(group) > 1 and any("digest" in entry for entry in group)]


def findings(before, after, blobs, link=None):
    """Why the step one directory takes from the base commit to the head commit is refused.

    before and after are the kustomization files the two commits have there,
    and link is what the base commit has in place of the directory, if it is
    not one there.
    """
    try:
        head = version(after, blobs)
    except verifier.Refused as error:
        return [str(error)]

    problems = [
        f"its images entries for {', '.join(entry['name'] for entry in group)} can apply to the same image, one after "
        "another, and one of them pins a digest: which tag and digest that image gets depends on their order, and "
        "this check does not decide it" for group in undecided(head)]

    def unknown(entry, why):
        return (f"{entry['name']}: nothing shows that digest {entry['digest']!a} is new beside newTag "
                f"{entry['newTag']!a}: in the base commit, {why}")

    pinned = [entry for entry in head if "newTag" in entry and "digest" in entry]
    if link:
        return problems + [unknown(entry, f"{link} is where this directory is, and what that led to is not read")
                           for entry in pinned]
    if not pinned or not before:
        return problems
    try:
        base = version(before, blobs)
    except verifier.Refused as error:
        return problems + [unknown(entry, error) for entry in pinned]

    unclear = [old for group in undecided(base) for old in group]
    for entry in pinned:
        # The manifests name the image, and both versions match that name.
        former = [old for old in base if verifier.overlap(old["name"], entry["name"])]
        kept = [old for old in former if old.get("digest") == entry["digest"] and old.get("newTag") != entry["newTag"]]
        if any(old in unclear for old in former) and any(old.get("digest") == entry["digest"] for old in base):
            problems.append(unknown(entry, "several images entries can apply to that image, one after another, and "
                                           "one of them pins a digest"))
        elif kept:
            tags = " or ".join(sorted({ascii(old["newTag"]) if "newTag" in old else "none" for old in kept}))
            problems.append(f"{entry['name']}: newTag went from {tags} to {entry['newTag']!a} "
                            f"and digest stayed {entry['digest']!a}")
    return problems


def main(arguments):
    if len(arguments) != 1:
        print(f"usage: {sys.argv[0]} <export>", file=sys.stderr)
        return 2
    export = Path(arguments[0])
    refused = 0
    try:
        base, base_links = kustomizations((export / "base.tree").read_bytes())
        head, head_links = kustomizations((export / "head.tree").read_bytes())
        # Kustomize reads through a symbolic link, and a submodule is another repository. This check follows
        # neither, so one anywhere under apps/ is refused, changed or not: what it leads to may have changed.
        for path, mode in sorted(head_links.items()):
            print(f"{shown(path)}: a symbolic link or a submodule (git mode {mode}): kustomize may read through "
                  "it, and this check does not follow it", file=sys.stderr)
            refused += 1
        changed = sorted(directory for directory, files in head.items()
                         if sorted(files) != sorted(base.get(directory, [])) and all(map(regular, files)))
        for directory in changed:
            files = head[directory]
            label = shown(f"{directory}/{files[0][0]}" if len(files) == 1 else directory)
            problems = findings(base.get(directory, []), files, export / "blobs",
                                leading_elsewhere(directory, base_links))
            for problem in problems:
                print(f"{label}: {problem}", file=sys.stderr)
            if not problems:
                print(f"ok: {label}", flush=True)
            refused += bool(problems)
    except (OSError, Malformed) as error:
        print(f"{export}: {error}", file=sys.stderr)
        return 2

    if refused:
        print(EXPLANATION, file=sys.stderr)
        return 1
    print(f"{len(changed)} changed kustomization file(s), none with a new tag beside an old digest")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
