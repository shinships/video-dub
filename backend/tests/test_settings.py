"""Test cho SettingsStore — lớp thay thế cho `Settings` frozen đọc env lúc import.

Rủi ro chính của lớp này không phải logic mà là THỨ TỰ ƯU TIÊN và ranh giới secret:
sai thứ tự thì người dùng sửa trong Cài đặt mà "không ăn"; sai ranh giới thì API key rơi
vào một file thường.
"""

import json

import pytest

from app.config import FIELD_SPECS, SECRET_FIELDS, Settings, SettingsError, SettingsStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Store độc lập: thư mục dữ liệu riêng, keychain giả trong RAM, env sạch.

    Phải xoá sạch env của mọi field: `app.config` nạp .env của chính dự án lúc import, nên
    nếu không xoá thì test đọc phải API key thật của lập trình viên — kết quả phụ thuộc máy,
    và một assertion hỏng sẽ in thẳng key ra log.
    """
    for spec in FIELD_SPECS.values():
        for env_name in spec.env:
            monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setenv("VIDEO_DUB_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("VIDEO_DUB_USE_KEYCHAIN", raising=False)

    vault: dict[str, str] = {}

    class _FakeKeyring:
        @staticmethod
        def get_password(service, name):
            return vault.get(name)

        @staticmethod
        def set_password(service, name, value):
            vault[name] = value

        @staticmethod
        def delete_password(service, name):
            vault.pop(name, None)

        @staticmethod
        def get_keyring():
            return object()

    monkeypatch.setattr("app.config._keyring_module", lambda: _FakeKeyring)
    instance = SettingsStore()
    instance.vault = vault  # để test kiểm tra thứ gì đã vào keychain
    return instance


def test_env_is_still_a_valid_source(store, monkeypatch):
    """CLI, script và chính conftest đều cấu hình qua env — không được bỏ nguồn này."""
    monkeypatch.setenv("VIDEO_DUB_WHISPER_MODEL", "large-v3")
    assert store.reload().whisper_model == "large-v3"
    assert store.sources()["whisper_model"] == "env"


def test_saved_value_beats_env(store, monkeypatch):
    """Giá trị bấm lưu trong app phải THẮNG biến môi trường. Nếu env thắng thì người dùng
    sửa trong Cài đặt sẽ không thấy gì đổi mà chẳng hiểu vì sao."""
    monkeypatch.setenv("VIDEO_DUB_WHISPER_MODEL", "medium")
    store.update({"whisper_model": "small"})

    assert store.current.whisper_model == "small"
    assert store.sources()["whisper_model"] == "file"


def test_keychain_beats_file_and_env_for_secrets(store, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "key-tu-env")
    assert store.reload().deepseek_api_key == "key-tu-env"

    store.update({"deepseek_api_key": "key-tu-keychain"})
    assert store.current.deepseek_api_key == "key-tu-keychain"
    assert store.sources()["deepseek_api_key"] == "keychain"


def test_secret_never_lands_in_settings_json(store):
    """settings.json là file thường, nằm cạnh DB trong data_dir. API key chỉ được đi vào
    keychain của hệ điều hành."""
    store.update({"gemini_api_key": "sk-bi-mat", "whisper_model": "small"})

    saved = json.loads(store.current.settings_file.read_text(encoding="utf-8"))
    assert saved == {"whisper_model": "small"}
    assert store.vault == {"gemini_api_key": "sk-bi-mat"}
    assert "sk-bi-mat" not in store.current.settings_file.read_text(encoding="utf-8")


def test_secret_hand_written_into_json_is_ignored(store, tmp_path):
    """Có người tự tay dán key vào settings.json thì cũng không đọc — đọc tức là ngầm chấp
    nhận nơi cất secret sai, rồi lần lưu sau lại ghi đè file đó."""
    (tmp_path / "settings.json").write_text(json.dumps({"gemini_api_key": "lo-key"}), encoding="utf-8")
    assert store.reload().gemini_api_key == ""


def test_clearing_a_secret_falls_back_to_env(store, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "key-tu-env")
    store.update({"deepseek_api_key": "key-rieng"})
    assert store.current.deepseek_api_key == "key-rieng"

    store.update({"deepseek_api_key": ""})
    assert store.vault == {}
    assert store.current.deepseek_api_key == "key-tu-env"


def test_update_refuses_field_outside_whitelist(store):
    """Whitelist là tường minh: field mới thêm vào Settings KHÔNG tự động sửa được từ ngoài."""
    with pytest.raises(SettingsError, match="không cho sửa"):
        store.update({"data_dir": "/tmp/o-dau-do"})
    with pytest.raises(SettingsError, match="không cho sửa"):
        store.update({"deepseek_base_url": "http://ke-tan-cong"})
    with pytest.raises(SettingsError, match="không cho sửa"):
        store.update({"__class__": "x"})


def test_update_validates_choices_and_types(store):
    with pytest.raises(SettingsError, match="chỉ nhận"):
        store.update({"stt_engine": "khong-ton-tai"})
    with pytest.raises(SettingsError, match="true/false"):
        store.update({"multi_speaker": "co"})
    with pytest.raises(SettingsError, match="chuỗi"):
        store.update({"vieneu_voice": 123})


def test_nothing_is_written_when_one_field_is_invalid(store):
    """Validate TRƯỚC khi ghi: một field sai không được để lại cấu hình lưu nửa vời."""
    with pytest.raises(SettingsError):
        store.update({"whisper_model": "small", "stt_engine": "sai"})

    assert not store.current.settings_file.exists()
    assert store.current.whisper_model == Settings.whisper_model


def test_choice_value_is_normalised_before_saving(store):
    """Người dùng gõ 'Google' mà lưu nguyên hoa thì `stt_engine == "google"` ở pipeline sẽ
    sai lặng lẽ: STT vẫn chạy Whisper dù UI hiện Google."""
    store.update({"stt_engine": "GOOGLE"})
    assert store.current.stt_engine == "google"


def test_corrupt_settings_file_does_not_break_startup(store, tmp_path, monkeypatch):
    (tmp_path / "settings.json").write_text("{ hong", encoding="utf-8")
    monkeypatch.setenv("VIDEO_DUB_WHISPER_MODEL", "base")

    assert store.reload().whisper_model == "base"  # lùi về env thay vì ném lỗi lúc khởi động


def test_proxy_sees_new_value_without_restart(store, monkeypatch):
    """Đây là mục đích tồn tại của proxy: ~90 chỗ gọi `settings.x` phải thấy giá trị mới
    ngay sau reload, không phải khởi động lại backend."""
    import app.config as config_module

    monkeypatch.setattr(config_module, "store", store)
    proxy = config_module.settings

    assert proxy.whisper_model == Settings.whisper_model
    store.update({"whisper_model": "large-v3"})
    assert proxy.whisper_model == "large-v3"


def test_data_dir_stays_env_only(store, tmp_path):
    """settings.json nằm BÊN TRONG data_dir nên data_dir không thể do chính nó quyết định."""
    assert "data_dir" not in {name for name, spec in FIELD_SPECS.items() if spec.label}
    assert store.current.data_dir == tmp_path


def test_keychain_missing_reports_instead_of_writing_plaintext(tmp_path, monkeypatch):
    """Không có keychain thì phải BÁO LỖI, tuyệt đối không âm thầm hạ cấp xuống ghi file."""
    monkeypatch.setenv("VIDEO_DUB_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("app.config._keyring_module", lambda: None)
    instance = SettingsStore()

    with pytest.raises(SettingsError, match="keychain"):
        instance.update({"gemini_api_key": "sk-bi-mat"})
    assert not (tmp_path / "settings.json").exists()


def test_bool_false_from_env_is_not_lost(store, monkeypatch):
    """Bẫy kinh điển: dùng truthiness để chọn nguồn thì False và 0 bị coi như 'chưa đặt'."""
    monkeypatch.setenv("VIDEO_DUB_TRANSLATE_FALLBACK", "false")
    monkeypatch.setenv("VIDEO_DUB_DEMUCS_SHIFTS", "0")
    current = store.reload()

    assert current.translate_fallback is False
    assert current.demucs_shifts == 0


def test_secret_fields_are_exactly_the_api_keys():
    assert SECRET_FIELDS == {"gemini_api_key", "deepseek_api_key", "openai_compat_api_key"}


def test_duration_limit_has_exactly_one_source(store):
    """Bốn chỗ từng nói bốn con số: hằng 14400s nhưng báo lỗi "30 phút", pipeline báo "4 giờ",
    UI ghi "30 phút". Đổi một chỗ thì mọi chỗ phải đổi theo."""
    import app.config as config_module
    from app.pipeline import PipelineError, check_duration

    store.update({"max_duration_minutes": 30})
    # `settings` là proxy đọc `config.store` lúc gọi, nên đổi store là pipeline thấy ngay.
    original = config_module.store
    config_module.store = store
    try:
        assert store.current.max_duration_seconds == 1800
        assert store.current.duration_limit_label == "30 phút"

        with pytest.raises(PipelineError, match="30 phút"):
            check_duration(1801)
        check_duration(1799)  # trong giới hạn -> không ném

        store.update({"max_duration_minutes": 240})
        assert store.current.duration_limit_label == "4 giờ"
        check_duration(1801)  # con số cũ nay hợp lệ, không còn chỗ nào giữ 30 phút
    finally:
        config_module.store = original


def test_duration_label_reads_naturally(store):
    for minutes, label in ((10, "10 phút"), (60, "1 giờ"), (90, "1 giờ 30 phút"), (240, "4 giờ")):
        store.update({"max_duration_minutes": minutes})
        assert store.current.duration_limit_label == label


def test_duration_limit_must_be_a_number(store):
    with pytest.raises(SettingsError, match="số nguyên"):
        store.update({"max_duration_minutes": "240"})
