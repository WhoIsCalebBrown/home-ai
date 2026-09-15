"""Generated multi-turn semantic scenario catalog; no production I/O."""


def build_catalog():
    catalog = []
    media = [
        ("Get Dumb and Dumber from 1994", "movie", "tmdb:8467"),
        ("Give me Dune from 1984", "movie", "tmdb:841"),
        ("Get season 2 of Severance", "tv", "tmdb_tv:95396/s2"),
        ("Get episode 8 of Severance", "tv", "tmdb_tv:95396/e8"),
        ("Get Rodeo by Travis Scott", "album", "musicbrainz:rodeo"),
        ("Get Dragon Ball Z Kai", "anime", "tvdb:79692"),
        ("How is Dumb and Dumber doing", "media_status", "tmdb:8467"),
        ("Is Rodeo in Plex", "media_status", "musicbrainz:rodeo"),
        ("Why is Severance not ready", "media_diagnose", "tmdb_tv:95396"),
        ("Try again", "media_retry", "same-workflow"),
    ]
    confirmations = ["yeah", "go for it", "please do", "okay", "I confirm", "no", "cancel", "maybe", "hold on", "not yet"]
    for index, (initial, kind, entity) in enumerate(media):
        for turn, reply in enumerate(confirmations):
            catalog.append({
                "scenario_id": f"media-{index:02d}-{turn:02d}",
                "initial_state": "ABSENT",
                "turns": [initial, reply, "How's it doing?", "Is it in Plex?"],
                "expected_domain": "media",
                "expected_kind": kind,
                "canonical_entity": entity,
                "writes_allowed": False,
            })
    return catalog

