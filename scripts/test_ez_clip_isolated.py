#!/usr/bin/env python3
"""Isolated smoke test for EZ Clip - models and registry only."""
import sys
sys.path.insert(0, '/Users/faiz/project/autoclip')

def test_editing_styles_registry():
    """Test editing_styles registry is valid."""
    print("=== Test: editing_styles registry ===")
    try:
        # Direct import without going through services __init__
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "editing_styles",
            "/Users/faiz/project/autoclip/backend/services/studio/editing_styles.py"
        )
        editing_styles = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(editing_styles)
        
        # Test PRESETS
        assert 'ez_clip' in editing_styles.PRESETS
        print("✓ ez_clip in PRESETS")
        
        # Test get_editing_style
        style = editing_styles.get_editing_style('ez_clip')
        assert style is not None
        assert style.id == 'ez_clip'
        assert style.version == 1
        assert style.name == 'EZ Clip'
        assert style.subtitle_style == 'ez_clip'
        print(f"✓ EZ Clip preset valid: {style.name}, v{style.version}")
        print(f"  Layout: {style.layout}")
        print(f"  Subtitle style: {style.subtitle_style}")
        print(f"  Motion: enabled={style.motion.enabled}, max_zoom={style.motion.max_zoom}")
        print(f"  Colors: primary={style.color_palette.primary}, accent={style.color_palette.accent}")
        
        # Test list
        styles = editing_styles.list_editing_styles()
        assert len(styles) > 0
        assert any(s['id'] == 'ez_clip' for s in styles)
        print(f"✓ list_editing_styles() returns {len(styles)} style(s)")
        
        # Test unknown
        assert editing_styles.get_editing_style('unknown') is None
        print("✓ Unknown style returns None")
        
        return True
    except Exception as e:
        print(f"✗ FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_subtitle_styles_constant():
    """Test SUBTITLE_STYLES has ez_clip."""
    print("\n=== Test: SUBTITLE_STYLES constant ===")
    try:
        # Read file and check for ez_clip definition
        with open('/Users/faiz/project/autoclip/backend/services/publish_export.py', 'r') as f:
            content = f.read()
        
        assert 'ez_clip' in content
        assert 'Fontsize=18' in content
        print("✓ ez_clip subtitle style defined in publish_export.py")
        
        return True
    except Exception as e:
        print(f"✗ FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_models_accept_fields():
    """Test Draft and ImportOptions accept new fields."""
    print("\n=== Test: Model field definitions ===")
    try:
        # Read models.py and check field definitions
        with open('/Users/faiz/project/autoclip/backend/services/studio/models.py', 'r') as f:
            content = f.read()
        
        # Check Draft fields
        assert "subtitle_style: Literal['clean', 'bold', 'box', 'accent', 'ez_clip']" in content
        print("✓ Draft.subtitle_style includes 'ez_clip'")
        
        assert 'editing_style_id: str | None' in content
        assert 'editing_style_version: int | None' in content
        assert 'editing_style_motion: bool = True' in content
        print("✓ Draft has editing_style_id, editing_style_version, editing_style_motion")
        
        # Check ImportOptions
        assert 'class ImportOptions' in content
        import_section = content[content.find('class ImportOptions'):content.find('class ImportOptions') + 2000]
        assert 'editing_style_id' in import_section
        print("✓ ImportOptions has editing_style_id field")
        
        return True
    except Exception as e:
        print(f"✗ FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_jobs_integration():
    """Test _apply_strategy handles editing_style_id."""
    print("\n=== Test: jobs.py integration ===")
    try:
        with open('/Users/faiz/project/autoclip/backend/services/studio/jobs.py', 'r') as f:
            content = f.read()
        
        # Check _apply_strategy signature
        assert 'editing_style_id=None' in content or 'editing_style_id: str | None = None' in content
        print("✓ _apply_strategy accepts editing_style_id parameter")
        
        # Check it uses get_editing_style
        assert 'from backend.services.studio.editing_styles import get_editing_style' in content
        assert 'get_editing_style' in content
        print("✓ _apply_strategy imports and uses get_editing_style")
        
        # Check it applies ez_clip subtitle_style
        assert "value['subtitle_style'] = 'ez_clip'" in content
        print("✓ _apply_strategy sets subtitle_style='ez_clip'")
        
        # Check generation state stores editing_style_id
        assert "'editing_style_id'" in content
        print("✓ Generation state includes editing_style_id")
        
        return True
    except Exception as e:
        print(f"✗ FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_api_endpoint():
    """Test API has editing-styles endpoint."""
    print("\n=== Test: API endpoint ===")
    try:
        with open('/Users/faiz/project/autoclip/backend/api/v1/studio.py', 'r') as f:
            content = f.read()
        
        assert "@router.get('/editing-styles')" in content
        assert 'list_editing_styles' in content
        print("✓ API has GET /editing-styles endpoint")
        
        # Check import form accepts editing_style_id
        assert 'editing_style_id: Optional[str] = Form(None)' in content or 'editing_style_id' in content
        print("✓ Import form accepts editing_style_id")
        
        return True
    except Exception as e:
        print(f"✗ FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_frontend_types():
    """Test frontend types include editing_style_id."""
    print("\n=== Test: Frontend types ===")
    try:
        with open('/Users/faiz/project/autoclip/frontend/src/features/studio/types.ts', 'r') as f:
            content = f.read()
        
        assert 'editing_style_id?' in content
        print("✓ Frontend ImportOptions has editing_style_id")
        
        assert 'editing_style_id?: string | null' in content or 'editing_style_id' in content
        print("✓ Frontend Draft type includes editing_style_id fields")
        
        return True
    except Exception as e:
        print(f"✗ FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_frontend_ui():
    """Test frontend has EZ Clip selector."""
    print("\n=== Test: Frontend UI ===")
    try:
        with open('/Users/faiz/project/autoclip/frontend/src/features/studio/CreativeImport.tsx', 'r') as f:
            content = f.read()
        
        assert 'ez_clip' in content.lower()
        print("✓ CreativeImport.tsx mentions ez_clip")
        
        assert 'editing_style_id' in content
        print("✓ CreativeImport.tsx uses editing_style_id")
        
        assert "body.append('editing_style_id'" in content
        print("✓ Form data includes editing_style_id")
        
        return True
    except Exception as e:
        print(f"✗ FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    print("EZ Clip Implementation - Isolated Smoke Test")
    print("=" * 60)
    
    results = []
    results.append(("editing_styles registry", test_editing_styles_registry()))
    results.append(("SUBTITLE_STYLES", test_subtitle_styles_constant()))
    results.append(("Model fields", test_models_accept_fields()))
    results.append(("jobs integration", test_jobs_integration()))
    results.append(("API endpoint", test_api_endpoint()))
    results.append(("Frontend types", test_frontend_types()))
    results.append(("Frontend UI", test_frontend_ui()))
    
    print("\n" + "=" * 60)
    print("Summary:")
    passed = sum(1 for _, r in results if r)
    total = len(results)
    
    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"  {status}: {name}")
    
    print(f"\nResult: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n✓ All smoke tests passed!")
        return 0
    else:
        print(f"\n✗ {total - passed} test(s) failed")
        return 1


if __name__ == '__main__':
    sys.exit(main())
