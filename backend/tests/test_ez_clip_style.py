"""Test EZ Clip editing style integration."""
import pytest
from backend.services.studio.editing_styles import get_editing_style, list_editing_styles, PRESETS
from backend.services.studio.models import Draft, ImportOptions
from backend.services.publish_export import SUBTITLE_STYLES


def test_ez_clip_preset_registered():
    """EZ Clip preset exists in registry."""
    style = get_editing_style('ez_clip')
    assert style is not None
    assert style.id == 'ez_clip'
    assert style.version == 1
    assert style.name == 'EZ Clip'
    assert style.subtitle_style == 'ez_clip'


def test_ez_clip_in_list():
    """EZ Clip appears in list_editing_styles."""
    styles = list_editing_styles()
    assert any(s['id'] == 'ez_clip' for s in styles)
    ez = next(s for s in styles if s['id'] == 'ez_clip')
    assert ez['name'] == 'EZ Clip'
    assert ez['version'] == 1


def test_ez_clip_subtitle_style_defined():
    """EZ Clip subtitle style exists in SUBTITLE_STYLES."""
    assert 'ez_clip' in SUBTITLE_STYLES
    style = SUBTITLE_STYLES['ez_clip']
    assert 'Fontsize=18' in style
    assert 'Bold=1' in style
    assert 'PrimaryColour=&H00FFFFFF' in style


def test_draft_accepts_ez_clip_subtitle_style():
    """Draft model accepts ez_clip as subtitle_style."""
    draft = Draft(
        id='test',
        title='Test',
        scenes=[{'id': 's1', 'label': 'Scene', 'start': 0, 'end': 10}],
        subtitle_style='ez_clip'
    )
    assert draft.subtitle_style == 'ez_clip'


def test_draft_accepts_editing_style_fields():
    """Draft model accepts editing_style_id and editing_style_version."""
    draft = Draft(
        id='test',
        title='Test',
        scenes=[{'id': 's1', 'label': 'Scene', 'start': 0, 'end': 10}],
        editing_style_id='ez_clip',
        editing_style_version=1
    )
    assert draft.editing_style_id == 'ez_clip'
    assert draft.editing_style_version == 1


def test_import_options_accepts_editing_style_id():
    """ImportOptions model accepts editing_style_id."""
    options = ImportOptions(
        platforms=['douyin'],
        editing_style_id='ez_clip'
    )
    assert options.editing_style_id == 'ez_clip'


def test_import_options_rejects_invalid_style_id():
    """ImportOptions rejects invalid editing_style_id pattern."""
    with pytest.raises(ValueError):
        ImportOptions(
            platforms=['douyin'],
            editing_style_id='Invalid-Style!'
        )


def test_get_editing_style_returns_none_for_unknown():
    """get_editing_style returns None for unknown style_id."""
    assert get_editing_style('unknown_style') is None
    assert get_editing_style(None) is None


def test_ez_clip_color_palette():
    """EZ Clip has correct color palette."""
    style = get_editing_style('ez_clip')
    assert style.color_palette.primary == '#FFFFFF'
    assert style.color_palette.accent == '#38BDF8'
    assert style.color_palette.background == '#111827'


def test_ez_clip_motion_settings():
    """EZ Clip has correct motion settings."""
    style = get_editing_style('ez_clip')
    assert style.motion.enabled is True
    assert style.motion.max_zoom == 1.06
    assert style.motion.cut_punch_in is False


def test_ez_clip_no_overlays_default():
    """EZ Clip has no overlays by default."""
    style = get_editing_style('ez_clip')
    assert style.overlays == []
    assert style.outro_enabled_default is False
