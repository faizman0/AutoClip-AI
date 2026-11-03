#!/usr/bin/env python3
"""Manual smoke test for EZ Clip editing style integration."""
import sys
import json

def test_editing_styles_module():
    """Test editing_styles module can be imported and returns valid data."""
    print("=== Test 1: Import editing_styles module ===")
    try:
        from backend.services.studio.editing_styles import (
            get_editing_style, list_editing_styles, PRESETS
        )
        print("✓ Module imported successfully")
        
        # Test PRESETS registry
        assert 'ez_clip' in PRESETS, "ez_clip not in PRESETS"
        print("✓ ez_clip registered in PRESETS")
        
        # Test get_editing_style
        style = get_editing_style('ez_clip')
        assert style is not None, "get_editing_style('ez_clip') returned None"
        assert style.id == 'ez_clip', f"Expected id 'ez_clip', got {style.id}"
        assert style.version == 1, f"Expected version 1, got {style.version}"
        print(f"✓ get_editing_style('ez_clip') returns valid preset")
        print(f"  Name: {style.name}")
        print(f"  Subtitle style: {style.subtitle_style}")
        print(f"  Motion: enabled={style.motion.enabled}, max_zoom={style.motion.max_zoom}")
        
        # Test list_editing_styles
        styles = list_editing_styles()
        assert isinstance(styles, list), "list_editing_styles() should return list"
        assert len(styles) > 0, "list_editing_styles() returned empty list"
        ez = next((s for s in styles if s['id'] == 'ez_clip'), None)
        assert ez is not None, "ez_clip not in list_editing_styles()"
        print(f"✓ list_editing_styles() returns {len(styles)} style(s)")
        
        # Test unknown style
        unknown = get_editing_style('unknown_style')
        assert unknown is None, "get_editing_style() should return None for unknown style"
        print("✓ get_editing_style() returns None for unknown style")
        
        return True
    except Exception as e:
        print(f"✗ FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_subtitle_styles():
    """Test EZ Clip subtitle style in SUBTITLE_STYLES."""
    print("\n=== Test 2: Subtitle styles ===")
    try:
        from backend.services.publish_export import SUBTITLE_STYLES
        
        assert 'ez_clip' in SUBTITLE_STYLES, "ez_clip not in SUBTITLE_STYLES"
        print("✓ ez_clip in SUBTITLE_STYLES")
        
        style = SUBTITLE_STYLES['ez_clip']
        assert 'Fontsize=18' in style, "Expected Fontsize=18"
        assert 'Bold=1' in style, "Expected Bold=1"
        assert 'PrimaryColour=&H00FFFFFF' in style, "Expected white primary color"
        print(f"✓ ez_clip subtitle style: {style[:80]}...")
        
        return True
    except Exception as e:
        print(f"✗ FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_draft_model():
    """Test Draft model accepts ez_clip fields."""
    print("\n=== Test 3: Draft model ===")
    try:
        from backend.services.studio.models import Draft
        
        # Test ez_clip subtitle_style
        draft = Draft(
            id='test',
            title='Test Draft',
            scenes=[{'id': 's1', 'label': 'Scene', 'start': 0, 'end': 10}],
            subtitle_style='ez_clip'
        )
        assert draft.subtitle_style == 'ez_clip'
        print("✓ Draft accepts subtitle_style='ez_clip'")
        
        # Test editing_style_id and version
        draft2 = Draft(
            id='test2',
            title='Test Draft 2',
            scenes=[{'id': 's1', 'label': 'Scene', 'start': 0, 'end': 10}],
            editing_style_id='ez_clip',
            editing_style_version=1,
            editing_style_motion=True
        )
        assert draft2.editing_style_id == 'ez_clip'
        assert draft2.editing_style_version == 1
        assert draft2.editing_style_motion is True
        print("✓ Draft accepts editing_style_id, editing_style_version, editing_style_motion")
        
        return True
    except Exception as e:
        print(f"✗ FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_import_options():
    """Test ImportOptions model accepts editing_style_id."""
    print("\n=== Test 4: ImportOptions model ===")
    try:
        from backend.services.studio.models import ImportOptions
        
        # Test with ez_clip
        options = ImportOptions(
            platforms=['douyin'],
            editing_style_id='ez_clip'
        )
        assert options.editing_style_id == 'ez_clip'
        print("✓ ImportOptions accepts editing_style_id='ez_clip'")
        
        # Test validation rejects invalid pattern
        try:
            ImportOptions(
                platforms=['douyin'],
                editing_style_id='Invalid-Style!'
            )
            print("✗ FAILED: Should reject invalid editing_style_id pattern")
            return False
        except ValueError:
            print("✓ ImportOptions rejects invalid editing_style_id pattern")
        
        return True
    except Exception as e:
        print(f"✗ FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_apply_strategy():
    """Test _apply_strategy function handles editing_style_id."""
    print("\n=== Test 5: _apply_strategy integration ===")
    try:
        from backend.services.studio.jobs import _apply_strategy
        from backend.services.studio.models import Draft
        
        base_draft = {
            'id': 'test',
            'title': 'Test',
            'scenes': [{'id': 's1', 'label': 'Scene', 'start': 0, 'end': 10}],
        }
        
        # Test without editing_style_id
        draft1 = _apply_strategy(base_draft, 'douyin')
        assert draft1.editing_style_id is None
        print("✓ _apply_strategy without editing_style_id works")
        
        # Test with ez_clip editing_style_id
        draft2 = _apply_strategy(base_draft, 'douyin', editing_style_id='ez_clip')
        assert draft2.editing_style_id == 'ez_clip'
        assert draft2.editing_style_version == 1
        assert draft2.subtitle_style == 'ez_clip'
        print("✓ _apply_strategy with editing_style_id='ez_clip' applies style")
        print(f"  subtitle_style: {draft2.subtitle_style}")
        print(f"  editing_style_version: {draft2.editing_style_version}")
        
        return True
    except Exception as e:
        print(f"✗ FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    print("EZ Clip Editing Style - Manual Smoke Test")
    print("=" * 60)
    
    results = []
    
    results.append(("editing_styles module", test_editing_styles_module()))
    results.append(("subtitle_styles", test_subtitle_styles()))
    results.append(("Draft model", test_draft_model()))
    results.append(("ImportOptions model", test_import_options()))
    results.append(("_apply_strategy", test_apply_strategy()))
    
    print("\n" + "=" * 60)
    print("Summary:")
    passed = sum(1 for _, r in results if r)
    total = len(results)
    
    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"  {status}: {name}")
    
    print(f"\nResult: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n✓ All tests passed!")
        return 0
    else:
        print(f"\n✗ {total - passed} test(s) failed")
        return 1


if __name__ == '__main__':
    sys.exit(main())
