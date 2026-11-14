"""A model of what kustomize does with the images list of a kustomization.

apply(entries, reference) is what an image reference of a manifest becomes.
Kustomize takes the entries in their order. It applies an entry when the name
of the entry, read as a pattern, matches the whole reference up to an optional
tag and an optional sha256 digest, and then:

- newName replaces the name;
- newTag and digest together replace the tag and the digest;
- newTag alone replaces the tag and drops the digest;
- digest alone replaces the digest and drops the tag;
- tagSuffix, with neither, is added to the tag and drops the digest.

The next entry meets the reference this one left.

The model follows kustomize v5.0.4, the one in kubectl v1.30.14. When it was
written, its output was the same as that of `kubectl kustomize` for 2000
random lists of up to four entries over 8000 references. Nothing in CI runs
kustomize against it. test_check_digest_pin_promotion.py uses it to say which
changes leave an old digest beside a new tag.
"""

import re

# What kustomize lets follow the name of an entry in a reference it matches.
TAIL = r"(:[a-zA-Z0-9_.{}-]*)?(@sha256:[a-zA-Z0-9_.{}-]*)?"


def split(reference):
    """The name, the tag and the digest of a reference, the way kustomize splits it."""
    slash = reference.find("/")
    start = slash if slash > 0 else 0
    at, colon = reference.find("@", start), reference.find(":", start)
    if at < 0 and colon < 0:
        return reference, "", ""
    if at >= 0 and (colon < 0 or at < colon):
        return reference[:at], "", reference[at + 1:]
    if at >= 0:
        return reference[:colon], reference[colon + 1:at], reference[at + 1:]
    return reference[:colon], reference[colon + 1:], ""


def apply(entries, reference):
    """The reference kustomize renders for an image reference of a manifest."""
    # Kustomize runs every entry twice in a row, once over the image fields it
    # finds by itself and once over the configured ones. Only tagSuffix shows.
    for entry in (entry for entry in entries for _ in range(2)):
        if not re.fullmatch(entry["name"] + TAIL, reference):
            continue
        name, tag, digest = split(reference)
        name = entry.get("newName") or name
        if entry.get("newTag") and entry.get("digest"):
            tag, digest = entry["newTag"], entry["digest"]
        elif entry.get("newTag"):
            tag, digest = entry["newTag"], ""
        elif entry.get("digest"):
            tag, digest = "", entry["digest"]
        elif entry.get("tagSuffix"):
            tag, digest = tag + entry["tagSuffix"], ""
        reference = name + (":" + tag if tag else "") + ("@" + digest if digest else "")
    return reference
