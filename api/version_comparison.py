"""SemVer ordering for upstream component releases; build labels are not updates."""
import re


def release_order(value):
    if not isinstance(value, str) or len(value) > 128:
        return None
    match = re.fullmatch(r'v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)'
                         r'(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?'
                         r'(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?', value)
    if not match:
        return None
    prerelease = match[4]
    identifiers = []
    for part in prerelease.split('.') if prerelease else ():
        if part.isdigit():
            if len(part) > 1 and part.startswith('0'):
                return None
            identifiers.append((0, int(part)))
        else:
            identifiers.append((1, part))
    return tuple(int(match[index]) for index in (1, 2, 3)) + (not bool(prerelease), tuple(identifiers))


def newer_release(installed, available):
    previous, candidate = release_order(installed), release_order(available)
    return previous is not None and candidate is not None and candidate > previous
