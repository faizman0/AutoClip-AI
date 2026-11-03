"""Editing style specifications and single source of truth for presets."""
from __future__ import annotations

from typing import Any, Dict, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field


class EditingStyleMotion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool = True
    max_zoom: float = Field(default=1.06, ge=1.0, le=1.2)
    cut_punch_in: bool = False


class EditingStyleColorPalette(BaseModel):
    model_config = ConfigDict(extra='forbid')
    primary: str = Field(default='#FFFFFF', pattern=r'^#[0-9a-fA-F]{6}$')
    secondary: str = Field(default='#CBD5E1', pattern=r'^#[0-9a-fA-F]{6}$')
    accent: str = Field(default='#38BDF8', pattern=r'^#[0-9a-fA-F]{6}$')
    background: str = Field(default='#111827', pattern=r'^#[0-9a-fA-F]{6}$')


class EditingStylePreset(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[a-z0-9_]{2,32}$')
    version: int = Field(default=1, ge=1)
    name: str = Field(min_length=1, max_length=60)
    layout: Literal['speaker_focus', 'full_frame', 'fit'] = 'speaker_focus'
    subtitle_style: Literal['clean', 'bold', 'box', 'accent', 'ez_clip'] = 'ez_clip'
    font_family: str = Field(default='NotoSans', max_length=60)
    subtitle_position: Literal['lower_third', 'center', 'bottom'] = 'lower_third'
    highlight_mode: Literal['keyword', 'none', 'accent'] = 'keyword'
    motion: EditingStyleMotion = Field(default_factory=EditingStyleMotion)
    overlays: list[str] = Field(default_factory=list, max_length=10)
    color_palette: EditingStyleColorPalette = Field(default_factory=EditingStyleColorPalette)
    outro_enabled_default: bool = False


# Built-in presets registry
PRESETS: Dict[str, EditingStylePreset] = {
    'ez_clip': EditingStylePreset(
        id='ez_clip',
        version=1,
        name='EZ Clip',
        layout='speaker_focus',
        subtitle_style='ez_clip',
        font_family='NotoSans',
        subtitle_position='lower_third',
        highlight_mode='keyword',
        motion=EditingStyleMotion(enabled=True, max_zoom=1.06, cut_punch_in=False),
        overlays=[],
        color_palette=EditingStyleColorPalette(
            primary='#FFFFFF',
            secondary='#CBD5E1',
            accent='#38BDF8',
            background='#111827',
        ),
        outro_enabled_default=False,
    )
}


def get_editing_style(style_id: Optional[str]) -> Optional[EditingStylePreset]:
    if not style_id:
        return None
    return PRESETS.get(style_id)


def list_editing_styles() -> list[dict[str, Any]]:
    return [preset.model_dump() for preset in PRESETS.values()]
