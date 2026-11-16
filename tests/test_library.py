"""Filename parsing tests for the local library scanner."""

import pytest

from app.library import parse_filename


@pytest.mark.parametrize(
    ("filename", "title", "year", "season", "episode", "media_type"),
    [
        ("Oppenheimer.2023.1080p.BluRay.x264.mkv", "Oppenheimer", 2023, None, None, "movie"),
        ("Interstellar (2014).mp4", "Interstellar", 2014, None, None, "movie"),
        ("The.Last.of.Us.S02E03.1080p.WEB-DL.mkv", "The Last of Us", None, 2, 3, "tv"),
        ("Breaking Bad - S05E14 - Ozymandias.mp4", "Breaking Bad", None, 5, 14, "tv"),
        ("Some.Show.S01E01.Pilot.720p.mkv", "Some Show", None, 1, 1, "tv"),
        ("Citizen Kane 1941 REMASTERED 1080p.mp4", "Citizen Kane", 1941, None, None, "movie"),
        ("No Metadata At All.avi", "No Metadata At All", None, None, None, "movie"),
    ],
)
def test_parse_filename(filename, title, year, season, episode, media_type):
    parsed = parse_filename(filename)
    assert parsed.title == title
    assert parsed.year == year
    assert parsed.season == season
    assert parsed.episode == episode
    assert parsed.media_type == media_type


def test_parse_filename_falls_back_to_stem():
    parsed = parse_filename("..._-_...mkv")
    assert parsed.title


def test_tv_title_uses_prefix_before_season_marker():
    # For TV, everything after SxxExx (year, quality tags) is episode metadata.
    parsed = parse_filename("Show.S01E02.2019.1080p.mkv")
    assert parsed.media_type == "tv"
    assert parsed.title == "Show"
    assert parsed.season == 1
    assert parsed.episode == 2
    assert parsed.year is None
