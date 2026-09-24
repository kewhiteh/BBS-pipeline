from pathlib import Path

from streamlit.testing.v1 import AppTest

APP_PATH = str(Path(__file__).parent.parent / "src" / "bbs_pipeline" / "ui" / "app.py")


def test_spatial_toggles_default_state():
    """Default state: spatial toggles defaulted to OFF, retaining baseline components."""
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=10)

    # Assert checkbox exists and is False by default
    checkboxes = at.checkbox
    spatial_cb = next(
        (cb for cb in checkboxes if cb.label == "Enable 50-Stop Spatial Processing"),
        None,
    )
    assert spatial_cb is not None
    assert spatial_cb.value is False

    # Assert that filter mode selectbox is not visible when disabled
    selectboxes = at.selectbox
    filter_mode_sb = next((sb for sb in selectboxes if sb.label == "Filter Mode"), None)
    assert filter_mode_sb is None


def test_spatial_elements_appear_when_toggled():
    """Assert spatial UI elements appear when toggled."""
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=10)

    # Enable spatial toggle
    spatial_cb = next(
        (cb for cb in at.checkbox if cb.label == "Enable 50-Stop Spatial Processing"),
        None,
    )
    spatial_cb.set_value(True)
    at.run(timeout=10)

    # Assert that filter mode selectbox appears
    selectboxes = at.selectbox
    filter_mode_sb = next((sb for sb in selectboxes if sb.label == "Filter Mode"), None)
    assert filter_mode_sb is not None
    assert filter_mode_sb.value == "STRICT_CONTAINMENT"

    # Assert that file uploaders appear
    uploaders = at.file_uploader
    poly_uploader = next((fu for fu in uploaders if "Spatial Filter" in fu.label), None)
    route_uploader = next(
        (fu for fu in uploaders if "Route Polyline" in fu.label), None
    )
    assert poly_uploader is not None
    assert route_uploader is not None


def test_invalid_geojson_error_toast():
    """Negative Assertion: Verify error handling/toast when uploaded spatial filter file is invalid GeoJSON."""
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=10)

    # Enable spatial toggle
    spatial_cb = next(
        (cb for cb in at.checkbox if cb.label == "Enable 50-Stop Spatial Processing"),
        None,
    )
    spatial_cb.set_value(True)
    at.run(timeout=10)

    poly_uploader = next(
        (fu for fu in at.file_uploader if "Spatial Filter" in fu.label), None
    )

    # Upload invalid JSON
    poly_uploader.set_value(
        [("invalid.geojson", b"invalid json {", "application/json")]
    )
    at.run(timeout=10)

    # Verify error is shown
    errors = at.error
    assert any("Invalid GeoJSON" in err.value for err in errors), (
        "Expected error message for invalid GeoJSON"
    )


def test_in_memory_serialization_vector_downloads():
    """In-memory serialization of vector downloads through Streamlit buffers."""
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=10)

    # Check that export formats include the vector types
    selectboxes = at.selectbox
    format_sb = next(
        (sb for sb in selectboxes if sb.label == "Serialization Format"), None
    )
    assert format_sb is not None
    options = format_sb.options
    assert "gpkg" in options
    assert "fgb" in options
    assert "parquet" in options
