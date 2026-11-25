"""Restart and disk-error regression checks for both camera managers."""
import importlib
import pytest


@pytest.mark.parametrize("module", ["modules.cameras_advanced", "modules.evo.camera_manager"])
def test_camera_assignments_survive_restart(tmp_path, module):
    camera = importlib.import_module(module)
    path = tmp_path / "settings" / "cameras.json"
    first = camera.AdvancedCameraManager(path)
    first.sources["cam2"].metadata.name = "Close-up"
    first.program_source = "cam2"
    first.recording_sources = ["cam2"]
    first.auto_switch_rules = {"enabled": False}
    first.save_configuration()
    assert path.is_file()
    reopened = camera.AdvancedCameraManager(path)
    assert reopened.program_source == "cam2"
    assert reopened.recording_sources == ["cam2"]
    assert reopened.auto_switch_rules == {"enabled": False}
    assert reopened.sources["cam2"].metadata.name == "Close-up"
    assert reopened.sources["cam2"].metadata.role.value == first.sources["cam2"].metadata.role.value
    assert reopened.sources["cam2"].get_stats()["transport"] == first.sources["cam2"].get_stats()["transport"]


@pytest.mark.parametrize("module", ["modules.cameras_advanced", "modules.evo.camera_manager"])
def test_camera_config_save_failure_is_not_reported_as_success(tmp_path, monkeypatch, module):
    camera = importlib.import_module(module)
    manager = camera.AdvancedCameraManager(tmp_path / "cameras.json")

    def disk_full(_path, _content):
        raise OSError("disk full")

    monkeypatch.setattr(camera, "write_json", disk_full)
    with pytest.raises(OSError, match="disk full"):
        manager.save_configuration()
