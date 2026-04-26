"""Lightweight HTTP config service for runtime UI/settings updates."""

import json


class WebConfigService:
    def __init__(self, cfg, config_path="config.json"):
        self._cfg = cfg
        self._config_path = config_path

    def enabled(self):
        return bool(self._cfg.get("web", {}).get("enabled", True))

    def port(self):
        web_cfg = self._cfg.get("web", {})
        try:
            return int(web_cfg.get("port", 80))
        except Exception:
            return 80

    def render_html(self, current_ip, ui_cfg, metrics_cfg):
        theme = str(ui_cfg.get("theme", "retro"))
        enabled_pages = ui_cfg.get("enabled_pages", [0, 1, 2, 3, 4, 5])
        pc_url = str(metrics_cfg.get("pc_url", ""))

        if not isinstance(enabled_pages, list):
            enabled_pages = [0, 1, 2, 3, 4, 5]

        parts = []
        parts.append("<!doctype html><html><head><meta charset='utf-8'>")
        parts.append("<meta name='viewport' content='width=device-width,initial-scale=1'>")
        parts.append("<title>ESP32 Config</title>")
        parts.append("</head><body>")
        parts.append("<h2>ESP32 Weather Station Config</h2>")
        parts.append("<p>Current IP: %s</p>" % self._html_escape(current_ip))
        parts.append("<p>Open <b>http://%s/</b></p>" % self._html_escape(current_ip))
        parts.append("<form method='POST' action='/save'>")
        parts.append("<label>Theme:</label>")
        parts.append("<select name='theme'>")
        for name in ("retro", "light", "high_contrast"):
            selected = " selected" if name == theme else ""
            parts.append("<option value='%s'%s>%s</option>" % (name, selected, name))
        parts.append("</select><br><br>")
        parts.append("<label>Visible pages:</label><br>")
        for page in range(6):
            checked = " checked" if page in enabled_pages else ""
            parts.append(
                "<label><input type='checkbox' name='page' value='%d'%s> Page %d</label><br>"
                % (page, checked, page)
            )
        parts.append("<br><label>PC metrics URL:</label><br>")
        parts.append(
            "<input type='text' name='pc_url' size='48' value='%s'>"
            % self._html_escape(pc_url)
        )
        parts.append("<br><br><button type='submit'>Save</button>")
        parts.append("</form></body></html>")
        return "".join(parts)

    def apply_form(self, form_dict):
        theme = str(form_dict.get("theme", "retro"))
        if theme not in ("retro", "light", "high_contrast"):
            theme = "retro"

        pages_raw = form_dict.get("page", [])
        if isinstance(pages_raw, str):
            pages_raw = [pages_raw]
        enabled_pages = self._normalize_pages(pages_raw)

        pc_url = str(form_dict.get("pc_url", "")).strip()
        if not pc_url:
            pc_url = str(self._cfg.get("metrics", {}).get("pc_url", ""))

        if "ui" not in self._cfg or not isinstance(self._cfg["ui"], dict):
            self._cfg["ui"] = {}
        self._cfg["ui"]["theme"] = theme
        self._cfg["ui"]["enabled_pages"] = enabled_pages

        if "metrics" not in self._cfg or not isinstance(self._cfg["metrics"], dict):
            self._cfg["metrics"] = {}
        self._cfg["metrics"]["pc_url"] = pc_url

        with open(self._config_path, "w", encoding="utf-8") as handle:
            json.dump(self._cfg, handle)

        return {
            "theme": theme,
            "enabled_pages": enabled_pages,
            "pc_url": pc_url,
        }

    def parse_form(self, body):
        text = body if isinstance(body, str) else str(body)
        out = {}
        if not text:
            return out

        pairs = text.split("&")
        for pair in pairs:
            if not pair:
                continue
            if "=" in pair:
                key, val = pair.split("=", 1)
            else:
                key, val = pair, ""

            key_dec = self._url_decode(key)
            val_dec = self._url_decode(val)

            old = out.get(key_dec)
            if old is None:
                out[key_dec] = val_dec
            elif isinstance(old, list):
                old.append(val_dec)
            else:
                out[key_dec] = [old, val_dec]

        return out

    @staticmethod
    def _normalize_pages(values):
        pages = []
        for value in values:
            try:
                page = int(value)
            except Exception:
                continue
            if 0 <= page <= 5 and page not in pages:
                pages.append(page)
        pages.sort()
        if not pages:
            return [0, 1, 2, 3, 4, 5]
        return pages

    @staticmethod
    def _from_hex(ch):
        code = ord(ch)
        if 48 <= code <= 57:
            return code - 48
        if 65 <= code <= 70:
            return code - 55
        if 97 <= code <= 102:
            return code - 87
        return -1

    def _url_decode(self, text):
        if not text:
            return ""
        out = []
        i = 0
        n = len(text)
        while i < n:
            ch = text[i]
            if ch == "+":
                out.append(" ")
                i += 1
                continue
            if ch == "%" and i + 2 < n:
                a = self._from_hex(text[i + 1])
                b = self._from_hex(text[i + 2])
                if a >= 0 and b >= 0:
                    out.append(chr((a << 4) | b))
                    i += 3
                    continue
            out.append(ch)
            i += 1
        return "".join(out)

    @staticmethod
    def _html_escape(text):
        s = str(text)
        s = s.replace("&", "&amp;")
        s = s.replace("<", "&lt;")
        s = s.replace(">", "&gt;")
        s = s.replace("\"", "&quot;")
        return s