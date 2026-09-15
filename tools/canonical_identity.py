"""Canonical media identity contract.

Centralizes the ad hoc identity dicts previously assembled inline, once per
media_type branch, inside media_plan_goal (movie / tv / anime / album). This
module changes representation only: it does not change which IDs are looked
up, which provider owns identity for a media type, or the canonical-ID-first
matching policy used by plex_match_canonical_media and the standard-request
executor. Canonical IDs remain sourced exactly as before (Radarr for movies,
Sonarr for TV/anime, Lidarr for music); this module only gives that inline
construction one shared, tested shape instead of four separate dict literals.

Adapted from HookReel's MetadataProvider field-contract idea (a single
normalized shape regardless of source), but keyed by canonical ID rather than
by provider-specific title dict, and assembled from Home-AI's existing
manager lookups rather than swapping in a third-party metadata provider.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

# The full set of fields this contract recognizes. media_plan_goal's four
# branches populate a different subset of these per media_type; unknown
# keyword arguments passed to build_canonical_identity() are silently
# dropped so call sites don't need per-branch guards.
CANONICAL_IDENTITY_FIELDS = (
    "media_type",
    "title",
    "year",
    "tmdb_id",
    "tvdb_id",
    "imdb_id",
    "musicbrainz_artist_id",
    "musicbrainz_release_group_id",
    "musicbrainz_release_id",
    "aliases",
    # Fields the existing movie/tv/anime/album branches also carry today.
    # Kept as first-class contract fields rather than reintroducing an ad hoc
    # dict for "everything the manager lookup happened to return".
    "artist",
    "foreign_album_id",
    "album_type",
    "series_type",
    "genres",
)

_EMPTY = (None, "", [])


@dataclass
class CanonicalIdentity:
    media_type: str | None = None
    title: str | None = None
    year: str | None = None
    tmdb_id: str | None = None
    tvdb_id: str | None = None
    imdb_id: str | None = None
    musicbrainz_artist_id: str | None = None
    musicbrainz_release_group_id: str | None = None
    musicbrainz_release_id: str | None = None
    aliases: list[str] = field(default_factory=list)
    artist: str | None = None
    foreign_album_id: str | None = None
    album_type: str | None = None
    series_type: str | None = None
    genres: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Only populate applicable fields -- unset/empty fields are omitted.

        No existing caller in this codebase checks for key presence on a
        canonical_identity dict (only .get(...)), so this is safe: a caller
        reading an omitted field via .get() still receives None, exactly as
        it did when the field was present with a None value.
        """
        out: dict = {}
        for key, value in asdict(self).items():
            if value in _EMPTY:
                continue
            out[key] = value
        return out

    def is_empty(self) -> bool:
        return not self.to_dict()

    def merge(self, other: "CanonicalIdentity") -> "CanonicalIdentity":
        """Entity-enrichment merge.

        Fills fields this identity does not yet have from `other`. Never
        overwrites an already-established scalar field (a canonical ID, once
        known, is never silently replaced by a later, possibly weaker,
        lookup) -- list fields (aliases/genres) are unioned instead.
        """
        merged = dict(asdict(self))
        for key, value in asdict(other).items():
            if value in _EMPTY:
                continue
            existing = merged.get(key)
            if isinstance(existing, list) or isinstance(value, list):
                existing_list = existing if isinstance(existing, list) else []
                value_list = value if isinstance(value, list) else []
                merged[key] = sorted(set(existing_list) | set(value_list))
            elif existing in _EMPTY:
                merged[key] = value
        return CanonicalIdentity(**merged)


def build_canonical_identity(**kwargs) -> CanonicalIdentity:
    """Factory used at every construction site instead of an ad hoc dict literal."""
    known = {key: value for key, value in kwargs.items() if key in CANONICAL_IDENTITY_FIELDS and value not in _EMPTY}
    return CanonicalIdentity(**known)


def from_dict(data: dict | None) -> CanonicalIdentity:
    if not data:
        return CanonicalIdentity()
    return build_canonical_identity(**data)
