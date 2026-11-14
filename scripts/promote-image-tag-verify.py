#!/usr/bin/env python3
"""Decide whether promote-image-tag.sh may install the edit it just made.

Usage: promote-image-tag-verify.py <kustomization> <edited-copy> <image> <tag>

promote-image-tag.sh rewrites a newTag by line. A line reader cannot tell
every way YAML has of giving an image entry a digest, so this reads both files
with a YAML parser and accepts the edit only when all of this holds:

- the kustomization is one YAML document without aliases, its top-level keys
  are distinct whatever their letter case, there is no `imageTags` list, and
  `images` is a list of entries whose keys are name, newTag, tagSuffix or
  digest, each at most once;
- every top-level key and every key and value of those entries is written out
  in full: one line, bare or in plain quotes, with no escape, tag or anchor,
  so that what a parser decodes is what the line shows;
- every name is a plain image name, without a tag, a digest or a pattern, and
  no entry renames an image with newName;
- exactly one entry names the image, it has no digest, and no other entry has
  a name that kustomize would also match against that image;
- the edited copy passes the same checks and parses to the same document with
  that entry's newTag replaced, and nothing else.

Kustomize renders an entry with a tag and a digest as image:tag@digest and the
cluster pulls by digest, so a new tag beside the old digest keeps running the
old image. A digest reaches an image in more ways than a `digest:` line under
its name. Kustomize reads `Digest` or `Images` as the field in lower case,
decodes escapes and `!!binary` in a name, follows merge keys and aliases, keeps
the last of two equal keys, appends the deprecated `imageTags` to `images`,
applies a later entry to the name an earlier one gave an image, and reads a
name as a pattern, so that `rbx.commerce` also matches `rbx-commerce`. Each of
these is refused here and not ignored.

This reads the images list of one kustomization. What a patch, a replacement or
a transformer file does to an image is outside it.

Exit status: 0 the edit may be installed, 2 refused, with the reason on
standard error. Reads the two files and writes nothing.
"""

import re
import sys

import yaml

ENTRY_KEYS = ("name", "newTag", "tagSuffix", "digest")
PLAIN_NAME = re.compile(r"[A-Za-z0-9_./-]+")
MAPPING = "tag:yaml.org,2002:map"
SEQUENCE = "tag:yaml.org,2002:seq"


class Refused(Exception):
    pass


def literal(text, node, what):
    """The value of a scalar whose text in the file is that value."""
    if not isinstance(node, yaml.ScalarNode):
        raise Refused(f"{what} is not a single value")
    written = text[node.start_mark.index:node.end_mark.index]
    if written not in (node.value, f'"{node.value}"', f"'{node.value}'"):
        raise Refused(f"{what} is not written out in full: {written!a}")
    return node.value


def overlap(one, other):
    """Whether kustomize can match two plain image names against one reference.

    It reads a name as a pattern, anchored, followed by an optional tag and
    digest. In a plain name only the dot is special: it stands for any
    character, also for the colon or the at sign that ends the name in a
    reference.
    """
    short, long = sorted((one, other), key=len)
    if any(a != b and "." not in (a, b) for a, b in zip(short, long)):
        return False
    return len(long) == len(short) or long[len(short)] == "."


def read(path):
    """The images entries of a kustomization and the parsed document."""
    try:
        with open(path, encoding="utf-8", newline="") as handle:
            text = handle.read()
        aliased = any(isinstance(token, yaml.AliasToken) for token in yaml.scan(text, Loader=yaml.SafeLoader))
        root = yaml.compose(text, Loader=yaml.SafeLoader)
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise Refused(f"it cannot be read as one YAML document ({type(error).__name__})") from error

    if aliased:
        raise Refused("it uses a YAML alias")
    if not isinstance(root, yaml.MappingNode) or root.tag != MAPPING:
        raise Refused("its top level is not a mapping")
    keys = [literal(text, key, "a top-level key") for key, _ in root.value]
    if "<<" in keys:
        raise Refused("it has a top-level merge key")
    if len({key.casefold() for key in keys}) != len(keys):
        raise Refused("two of its top-level keys differ at most in letter case")
    if any(key.casefold() == "imagetags" for key in keys):
        raise Refused("it uses the deprecated imageTags list")
    if "images" not in keys:
        raise Refused("it has no top-level key spelled images")
    images = root.value[keys.index("images")][1]
    if not isinstance(images, yaml.SequenceNode) or images.tag != SEQUENCE:
        raise Refused("images is not a list")

    entries = []
    for node in images.value:
        if not isinstance(node, yaml.MappingNode) or node.tag != MAPPING:
            raise Refused("an images entry is not a mapping")
        entry = {}
        for key, value in node.value:
            field = literal(text, key, "a key of an images entry")
            if field == "newName":
                raise Refused("an images entry renames an image with newName")
            if field not in ENTRY_KEYS:
                raise Refused(f"an images entry has the key {field!a}")
            if field in entry:
                raise Refused(f"an images entry has the key {field!a} twice")
            entry[field] = literal(text, value, f"the {field} of an images entry")
        if "name" not in entry:
            raise Refused("an images entry has no name")
        if not PLAIN_NAME.fullmatch(entry["name"]):
            raise Refused(f"the name of an images entry is not a plain image name: {entry['name']!a}")
        entries.append(entry)

    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise Refused(f"it cannot be loaded ({type(error).__name__})") from error
    return entries, document


def verify(kustomization, edited, image, tag):
    entries, expected = read(kustomization)
    named = [index for index, entry in enumerate(entries) if entry["name"] == image]
    if len(named) != 1:
        raise Refused(f"{len(named)} of its images entries name {image}")
    index = named[0]
    for entry in entries[:index] + entries[index + 1:]:
        if overlap(entry["name"], image):
            raise Refused(f"its images entry for {entry['name']} also matches {image}: kustomize reads a name as a pattern")
    if "digest" in entries[index]:
        raise Refused(
            f"it pins {image} by digest; refusing to change newTag alone\n"
            "the cluster pulls by digest, so a new tag beside the old digest keeps running the old image\n"
            "promote this image by hand: set newTag and the digest of that tag in the same commit"
        )
    if "newTag" not in entries[index]:
        raise Refused(f"its entry for {image} has no newTag")

    expected["images"][index]["newTag"] = tag
    try:
        _, result = read(edited)
    except Refused as error:
        raise Refused(f"after the edit, {error}") from error
    if result != expected:
        raise Refused(f"the edit changed something other than the newTag of {image}")


def main(arguments):
    if len(arguments) != 4:
        print(f"usage: {sys.argv[0]} <kustomization> <edited-copy> <image> <tag>", file=sys.stderr)
        return 2
    try:
        verify(*arguments)
    except Refused as error:
        print(f"{arguments[0]}: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
