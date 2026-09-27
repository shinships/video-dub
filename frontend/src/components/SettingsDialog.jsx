import { useEffect, useMemo, useState } from "react";
import { FloppyDisk, Key, SpinnerGap, WarningCircle } from "@phosphor-icons/react";
import { api, putJson } from "../api.js";

const GROUP_LABELS = {
  translate: "Dịch thuật",
  voice: "Giọng đọc",
  engine: "Xử lý",
  google: "Google Cloud",
};

/** Nguồn của giá trị đang hiển thị. Người dùng cần biết vì sao một ô có sẵn giá trị mà họ
 * không tự điền — nhất là khi giá trị đó đến từ .env chứ không phải từ màn hình này. */
const SOURCE_NOTE = {
  env: "đang lấy từ .env",
  keychain: "đã lưu trong keychain",
  file: "",
  default: "",
};

export function SettingsDialog({ onClose, onSaved }) {
  const [config, setConfig] = useState(null);
  const [draft, setDraft] = useState({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api("/settings")
      .then(setConfig)
      .catch((err) => setError(err.message));
  }, []);

  const groups = useMemo(() => {
    if (!config) return [];
    const bucket = new Map();
    for (const field of config.fields) {
      if (!bucket.has(field.group)) bucket.set(field.group, []);
      bucket.get(field.group).push(field);
    }
    return [...bucket.entries()];
  }, [config]);

  // Chỉ gửi những field thực sự đổi: gửi cả form sẽ ghi đè giá trị đang đến từ .env thành
  // giá trị lưu trong settings.json, tức là âm thầm "đóng băng" cấu hình của người dùng.
  const changed = Object.keys(draft);

  const save = async () => {
    if (!changed.length) return onClose();
    setSaving(true);
    setError("");
    try {
      const next = await putJson("/settings", draft);
      setConfig(next);
      setDraft({});
      onSaved(next);
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  const valueOf = (field) =>
    field.name in draft ? draft[field.name] : config.values[field.name];

  const setValue = (name, value) => setDraft((current) => ({ ...current, [name]: value }));

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <section className="settings-dialog" onMouseDown={(event) => event.stopPropagation()}>
        <header>
          <h2>Cài đặt</h2>
          <p>Thay đổi có hiệu lực ngay, không cần khởi động lại backend.</p>
        </header>

        {!config && !error && <p className="settings-loading">Đang tải cài đặt…</p>}

        {config && (
          <div className="settings-body">
            {config.missing?.length > 0 && (
              <div className="settings-missing">
                <WarningCircle weight="duotone" />
                <ul>
                  {config.missing.map((item) => (
                    <li key={item.key}>{item.message}</li>
                  ))}
                </ul>
              </div>
            )}

            {!config.keychain && (
              <p className="settings-warn">
                Máy này chưa dùng được keychain nên không lưu được API key từ đây. Hãy cài gói{" "}
                <code>keyring</code>, hoặc khai báo key trong tệp <code>.env</code>.
              </p>
            )}

            {groups.map(([group, fields]) => (
              <fieldset key={group}>
                <legend>{GROUP_LABELS[group] || group}</legend>
                {fields.map((field) => {
                  const note = SOURCE_NOTE[config.sources[field.name]] || "";
                  const isSecret = field.secret;
                  const alreadySet = config.secrets_set?.[field.name];
                  return (
                    <label className="settings-row" key={field.name}>
                      <span className="settings-label">
                        {isSecret && <Key weight="fill" />}
                        {field.label}
                        {note && <em>{note}</em>}
                      </span>

                      {field.type === "bool" ? (
                        <input
                          type="checkbox"
                          checked={Boolean(valueOf(field))}
                          onChange={(event) => setValue(field.name, event.target.checked)}
                        />
                      ) : field.choices.length > 0 ? (
                        <select
                          value={valueOf(field)}
                          onChange={(event) => setValue(field.name, event.target.value)}
                        >
                          {field.choices.map((choice) => (
                            <option key={choice} value={choice}>
                              {choice}
                            </option>
                          ))}
                        </select>
                      ) : field.type === "number" ? (
                        <input
                          type="number"
                          min="1"
                          value={valueOf(field)}
                          onChange={(event) =>
                            // Field kiểu int: gửi chuỗi lên sẽ bị backend từ chối.
                            setValue(field.name, Number(event.target.value) || 0)
                          }
                        />
                      ) : (
                        <input
                          type={isSecret ? "password" : "text"}
                          value={valueOf(field)}
                          autoComplete={isSecret ? "new-password" : "off"}
                          placeholder={
                            isSecret && alreadySet ? "••••••••  (đã lưu — gõ để thay)" : ""
                          }
                          onChange={(event) => setValue(field.name, event.target.value)}
                        />
                      )}

                      {field.help && <small>{field.help}</small>}
                    </label>
                  );
                })}
              </fieldset>
            ))}
          </div>
        )}

        {error && <p className="settings-error">{error}</p>}

        <footer>
          <button className="text-button" type="button" onClick={onClose} disabled={saving}>
            Đóng
          </button>
          <button className="primary-action" type="button" onClick={save} disabled={saving || !config}>
            {saving ? <SpinnerGap className="spin" /> : <FloppyDisk />}
            {changed.length ? `Lưu ${changed.length} thay đổi` : "Lưu"}
          </button>
        </footer>
      </section>
    </div>
  );
}
