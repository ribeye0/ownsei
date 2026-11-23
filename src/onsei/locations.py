"""Readable short references resolved against this installation, without a new registry."""

from urllib.parse import quote, unquote


def unique_prefix(value, candidates):
    if value is None:
        return "~"
    if value not in candidates:
        raise ValueError("定位資料已不存在，請重新載入結果")
    for length in range(min(6, len(value)), len(value) + 1):
        prefix = value[:length]
        if sum(c.startswith(prefix) for c in candidates) == 1:
            return quote(prefix, safe="")
    raise ValueError("無法建立唯一定位碼")


def resolve_prefix(prefix, candidates):
    if prefix == "~":
        return None
    prefix = unquote(prefix)
    if not prefix:
        raise ValueError("Invalid empty location prefix")
    found = [c for c in candidates if c.startswith(prefix)]
    if len(found) != 1:
        raise ValueError("定位碼已失效或有歧義，請使用完整診斷")
    return found[0]


def location_candidates(store, location):
    tracks = [t.id for t in store.tracks()]
    versions = [v["version"] for v in store.transcript_versions(location["track"])]
    translations = [v["id"] for v in store.translation_versions(location["track"], location["transcript"])]
    cues = (
        [c.id for c in store.cues(location["track"], location["transcript"])]
        if location["transcript"]
        else []
    )
    return [tracks, versions, translations, cues]


def compact_location(store, location, projects):
    candidates = [projects, *location_candidates(store, location)]
    pieces = [
        unique_prefix(location[k], pool)
        for k, pool in zip(("project", "track", "transcript", "translation", "cue"), candidates)
    ]
    if location["cue"]:
        cues = store.cues(location["track"], location["transcript"])
        cue = next(c for c in cues if c.id == location["cue"])
        # Cue IDs include the entire track/version prefix. A stable ordinal
        # within the already pinned transcript is much shorter.
        if sum(c.ordinal == cue.ordinal for c in cues) == 1:
            pieces[-1] = f"@{cue.ordinal + 1}"
    return "OW1/" + "/".join(pieces)


def resolve_location(code, projects, open_project):
    pieces = code.split("/")
    if len(pieces) != 6 or pieces[0] != "OW1":
        raise ValueError("Invalid location code")
    project = resolve_prefix(pieces[1], projects)
    if not project:
        raise ValueError("Invalid project code")
    store = open_project(project)
    location = {"project": project, "track": resolve_prefix(pieces[2], [t.id for t in store.tracks()])}
    if not location["track"]:
        raise ValueError("Invalid track code")
    location["transcript"] = resolve_prefix(
        pieces[3], [v["version"] for v in store.transcript_versions(location["track"])]
    )
    location["translation"] = resolve_prefix(
        pieces[4], [v["id"] for v in store.translation_versions(location["track"], location["transcript"])]
    )
    cues = store.cues(location["track"], location["transcript"])
    if pieces[5].startswith("@"):
        number = pieces[5][1:]
        found = [c.id for c in cues if number.isdigit() and c.ordinal + 1 == int(number)]
        if len(found) != 1:
            raise ValueError("定位句段已不存在或有歧義，請使用完整診斷")
        location["cue"] = found[0]
    else:
        # Keep older copied references usable.
        location["cue"] = resolve_prefix(pieces[5], [c.id for c in cues])
    return location
