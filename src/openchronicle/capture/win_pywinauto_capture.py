"""Windows UI Automation capture via pywinauto (UIA backend).

Output schema matches the AX-tree JSON shape used by ``s1_parser`` /
``ax_models`` / timeline (stable field names and pruning rules across
platforms).
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
from datetime import datetime, timezone
from typing import Any

from ..logger import get

logger = get("openchronicle.capture")

_VALUE_MAX = 1000
_MAX_CHILDREN = 200

# UIA ControlType bare name → AX role (ROLE_MAP aligned with mac-ax-helper vocabulary)
_ROLE_MAP: dict[str, str] = {
    "Window": "AXWindow",
    "Pane": "AXGroup",
    "Group": "AXGroup",
    "Custom": "AXGroup",
    "TitleBar": "AXGroup",
    "StatusBar": "AXGroup",
    "Calendar": "AXGroup",
    "SemanticZoom": "AXGroup",
    "Document": "AXTextArea",
    "Edit": "AXEdit",
    "Text": "AXStaticText",
    "Hyperlink": "AXLink",
    "Button": "AXButton",
    "SplitButton": "AXButton",
    "MenuItem": "AXMenuItem",
    "Menu": "AXMenu",
    "MenuBar": "AXMenuBar",
    "CheckBox": "AXCheckBox",
    "RadioButton": "AXRadioButton",
    "ComboBox": "AXComboBox",
    "List": "AXList",
    "ListItem": "AXRow",
    "Tree": "AXOutline",
    "TreeItem": "AXRow",
    "DataGrid": "AXOutline",
    "DataItem": "AXRow",
    "Table": "AXOutline",
    "Header": "AXHeading",
    "HeaderItem": "AXHeading",
    "Tab": "AXTabGroup",
    "TabItem": "AXTab",
    "ToolBar": "AXToolbar",
    "AppBar": "AXToolbar",
    "Image": "AXImage",
    "ScrollBar": "AXScrollBar",
    "Slider": "AXSlider",
    "ProgressBar": "AXProgressIndicator",
    "Spinner": "AXIncrementor",
    "Separator": "AXSplitter",
    "ToolTip": "AXToolTip",
    "Thumb": "AXValueIndicator",
}

_DROP_ROLES = frozenset({"AXImage", "AXScrollBar", "AXValueIndicator", "AXSplitter"})
_CONTAINER_ROLES = frozenset(
    {
        "AXGroup",
        "AXSplitGroup",
        "AXScrollArea",
        "AXList",
        "AXOutline",
        "AXBrowser",
        "AXDrawer",
        "AXSheet",
        "AXToolbar",
    }
)


def _com_init() -> None:
    try:
        import pythoncom  # type: ignore[import-not-found]

        pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
    except Exception:
        pass


def _com_fini() -> None:
    try:
        import pythoncom  # type: ignore[import-not-found]

        pythoncom.CoUninitialize()
    except Exception:
        pass


def _foreground_hwnd_pid() -> tuple[int, int]:
    if os.name != "nt":
        return (0, 0)
    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        user32.GetForegroundWindow.argtypes = []
        user32.GetForegroundWindow.restype = wt.HWND
        user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
        user32.GetWindowThreadProcessId.restype = wt.DWORD
        hwnd = user32.GetForegroundWindow() or 0
        if not hwnd:
            return (0, 0)
        pid = wt.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return (int(hwnd), int(pid.value))
    except Exception as exc:  # noqa: BLE001
        logger.debug("foreground HWND query failed: %s", exc)
        return (0, 0)


def _control_type_bare(wrapper: Any) -> str:
    """Return bare UIA control type name (e.g. 'Edit') for role mapping."""
    try:
        ct = wrapper.element_info.control_type
    except Exception:  # noqa: BLE001
        return "Unknown"
    try:
        from pywinauto.uia_defines import control_types as ct_mod  # type: ignore[import-not-found]

        for name in dir(ct_mod):
            if name.startswith("_"):
                continue
            if getattr(ct_mod, name, None) == ct:
                return name
    except Exception:  # noqa: BLE001
        pass
    return str(ct)


def _map_role(bare: str) -> str:
    return _ROLE_MAP.get(bare, f"AX{bare}")


def _is_window_control_type(wrapper: Any) -> bool:
    try:
        from pywinauto.uia_defines import control_types as ct_mod  # type: ignore[import-not-found]

        return wrapper.element_info.control_type == ct_mod.Window
    except Exception:  # noqa: BLE001
        return _control_type_bare(wrapper) == "Window"


def _element_value(wrapper: Any) -> str:
    for attr in ("get_value",):
        fn = getattr(wrapper, attr, None)
        if callable(fn):
            try:
                v = fn()
                if v is None:
                    continue
                s = str(v).strip()
                if len(s) > _VALUE_MAX:
                    return s[:_VALUE_MAX] + "..."
                return s
            except Exception:  # noqa: BLE001
                continue
    try:
        ts = wrapper.get_toggle_state()
        if ts is not None:
            return str(ts)
    except Exception:  # noqa: BLE001
        pass
    return ""


def _is_password_field(wrapper: Any) -> bool:
    try:
        ei = wrapper.element_info
        if getattr(ei, "is_password", False):
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _focused_is_password() -> bool:
    try:
        from pywinauto import Desktop  # type: ignore[import-not-found]

        d = Desktop(backend="uia")
        get_active = getattr(d, "get_active", None)
        if get_active is None:
            return False
        fe = get_active()
        return _is_password_field(fe)
    except Exception:  # noqa: BLE001
        return False


def _element_tree(wrapper: Any, current_depth: int, max_depth: int, raw: bool) -> dict[str, Any] | None:
    if max_depth > 0 and current_depth >= max_depth:
        return None

    try:
        bare = _control_type_bare(wrapper)
        role = _map_role(bare)
        title = (getattr(wrapper.element_info, "name", None) or "").strip()
        identifier = (getattr(wrapper.element_info, "automation_id", None) or "").strip()
        is_secure = _is_password_field(wrapper)
        value = "[REDACTED]" if is_secure else _element_value(wrapper).strip()
    except Exception:  # noqa: BLE001
        return None

    if not raw and role in _DROP_ROLES:
        return None

    child_list: list[dict[str, Any]] = []
    if max_depth <= 0 or current_depth + 1 < max_depth:
        try:
            children = wrapper.children()
        except Exception:  # noqa: BLE001
            children = []
        count = 0
        for ch in children:
            if count >= _MAX_CHILDREN:
                break
            sub = _element_tree(ch, current_depth + 1, max_depth, raw)
            if sub is not None:
                child_list.append(sub)
            count += 1

    has_text = bool(title) or bool(value)

    if not raw and role in _CONTAINER_ROLES and not has_text:
        if len(child_list) == 1:
            return child_list[0]
        if len(child_list) == 0:
            return None

    if not raw and not has_text and len(child_list) == 0:
        return None

    node: dict[str, Any] = {"role": role}
    if title:
        node["title"] = title
    if identifier:
        node["identifier"] = identifier
    if value:
        node["value"] = value
    if child_list:
        node["children"] = child_list
    return node


def _window_elements(root_wrapper: Any, max_depth: int, raw: bool) -> list[dict[str, Any]]:
    elements: list[dict[str, Any]] = []
    try:
        children = root_wrapper.children()
    except Exception:  # noqa: BLE001
        return elements
    for ch in children:
        el = _element_tree(ch, 0, max_depth, raw)
        if el is not None:
            elements.append(el)
    return elements


def _window_data(root_wrapper: Any, max_depth: int, raw: bool, is_focused: bool) -> dict[str, Any]:
    try:
        title = (root_wrapper.element_info.name or "").strip()
    except Exception:  # noqa: BLE001
        title = ""
    if _focused_is_password():
        title = "[REDACTED]"
    win: dict[str, Any] = {"title": title}
    if is_focused:
        win["focused"] = True
    elems = _window_elements(root_wrapper, max_depth, raw)
    if elems:
        win["elements"] = elems
    return win


def _app_from_pid(pid: int, is_frontmost: bool) -> dict[str, Any]:
    app: dict[str, Any] = {
        "pid": int(pid),
        "name": "",
        "bundle_id": "",
        "is_frontmost": bool(is_frontmost),
        "windows": [],
    }
    if pid <= 0:
        return app
    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if h:
            try:
                buf = ctypes.create_unicode_buffer(2048)
                size = wt.DWORD(len(buf))
                if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                    app["bundle_id"] = buf.value
                base = os.path.basename(buf.value or "")
                if base:
                    app["name"] = os.path.splitext(base)[0]
            finally:
                kernel32.CloseHandle(h)
    except Exception:  # noqa: BLE001
        pass
    return app


def _resolve_window_wrapper(hwnd: int) -> Any | None:
    from pywinauto import Desktop  # type: ignore[import-not-found]

    if not hwnd:
        return None
    try:
        d = Desktop(backend="uia")
        el = d.window(handle=int(hwnd))
        el.wait("exists", timeout=2)
        if _is_window_control_type(el):
            return el
        cur = el
        for _ in range(64):
            try:
                cur = cur.parent()
            except Exception:  # noqa: BLE001
                return None
            if _is_window_control_type(cur):
                return cur
    except Exception as exc:  # noqa: BLE001
        logger.debug("resolve window wrapper failed: %s", exc)
    return None


def _enum_visible_top_windows() -> list[tuple[int, int]]:
    """Return (hwnd, pid) for visible top-level windows."""
    out: list[tuple[int, int]] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def _cb(hwnd: int, _lparam: int) -> bool:
        try:
            user32 = ctypes.windll.user32  # type: ignore[attr-defined]
            if not user32.IsWindowVisible(hwnd):
                return True
            pid = wt.DWORD(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            out.append((int(hwnd), int(pid.value)))
        except Exception:  # noqa: BLE001
            pass
        return True

    try:
        ctypes.windll.user32.EnumWindows(_cb, 0)  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001
        logger.warning("EnumWindows failed: %s", exc)
    return out


def _build_payload(
    *,
    all_visible: bool,
    app_name: str | None,
    focused_window_only: bool,
    anchor_hwnd: int,
    anchor_pid: int,
    depth: int,
    raw: bool,
) -> dict[str, Any]:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    output: dict[str, Any] = {"timestamp": ts, "apps": []}

    hwnd, pid = anchor_hwnd, anchor_pid
    if not all_visible and not app_name:
        if not hwnd:
            hwnd, pid = _foreground_hwnd_pid()

    try:
        if all_visible:
            fg_hwnd, fg_pid = _foreground_hwnd_pid()
            by_pid: dict[int, list[int]] = {}
            for w_hwnd, w_pid in _enum_visible_top_windows():
                by_pid.setdefault(w_pid, []).append(w_hwnd)

            for w_pid, hwnds in by_pid.items():
                is_front = w_pid == fg_pid
                app_data = _app_from_pid(w_pid, is_front)
                window_dicts: list[dict[str, Any]] = []
                for w_hwnd in hwnds:
                    rw = _resolve_window_wrapper(w_hwnd)
                    if rw is None:
                        continue
                    is_focused = bool(is_front and int(w_hwnd) == int(fg_hwnd))
                    try:
                        window_dicts.append(_window_data(rw, depth, raw, is_focused))
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("window_data failed hwnd=%s: %s", w_hwnd, exc)
                app_data["windows"] = window_dicts
                output["apps"].append(app_data)
        elif app_name:
            from pywinauto import Desktop  # type: ignore[import-not-found]
            from pywinauto.uia_defines import control_types as ct_mod  # type: ignore[import-not-found]

            d = Desktop(backend="uia")
            try:
                w = d.window(title=app_name, control_type=ct_mod.Window)
                w.wait("exists", timeout=2)
            except Exception:  # noqa: BLE001
                w = None
            if w is not None:
                try:
                    w_pid = int(w.process_id())
                except Exception:  # noqa: BLE001
                    w_pid = 0
                app_data = _app_from_pid(w_pid, True)
                app_data["windows"] = [_window_data(w, depth, raw, True)]
                output["apps"].append(app_data)
        else:
            if hwnd and pid:
                foreground_window = _resolve_window_wrapper(hwnd)
                app_data = _app_from_pid(pid, True)
                if focused_window_only:
                    if foreground_window is not None:
                        app_data["windows"] = [
                            _window_data(foreground_window, depth, raw, True)
                        ]
                else:
                    window_dicts: list[dict[str, Any]] = []
                    for w_hwnd, w_pid in _enum_visible_top_windows():
                        if w_pid != pid:
                            continue
                        rw = _resolve_window_wrapper(w_hwnd)
                        if rw is None:
                            continue
                        is_focused = False
                        try:
                            is_focused = int(w_hwnd) == int(hwnd)
                        except Exception:  # noqa: BLE001
                            pass
                        try:
                            window_dicts.append(_window_data(rw, depth, raw, is_focused))
                        except Exception as exc:  # noqa: BLE001
                            logger.debug("window_data failed: %s", exc)
                    if not window_dicts and foreground_window is not None:
                        window_dicts.append(
                            _window_data(foreground_window, depth, raw, True)
                        )
                    app_data["windows"] = window_dicts
                output["apps"].append(app_data)
    except Exception as exc:  # noqa: BLE001
        output["error"] = str(exc)

    return output


def _strip_frame_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_frame_fields(v) for k, v in value.items() if k != "frame"}
    if isinstance(value, list):
        return [_strip_frame_fields(item) for item in value]
    return value


class WinPywinautoProvider:
    """In-process UIA tree capture using pywinauto."""

    def __init__(self, *, depth: int, timeout: int, raw: bool = False) -> None:
        self._depth = depth
        self._timeout = timeout  # reserved for parity with mac helper / future use
        self._raw = raw

    @property
    def available(self) -> bool:
        try:
            import pywinauto  # noqa: F401, PLC0415

            return os.name == "nt"
        except ImportError:
            return False

    def capture_frontmost(
        self,
        *,
        focused_window_only: bool = True,
        anchor_hwnd: int = 0,
        anchor_pid: int = 0,
    ) -> Any | None:
        return self._run(
            all_visible=False,
            focused_window_only=focused_window_only,
            anchor_hwnd=anchor_hwnd,
            anchor_pid=anchor_pid,
        )

    def capture_all_visible(self) -> Any | None:
        return self._run(all_visible=True)

    def capture_app(
        self, app_name: str, *, focused_window_only: bool = True
    ) -> Any | None:
        return self._run(
            all_visible=False,
            app_name=app_name,
            focused_window_only=focused_window_only,
        )

    def _run(
        self,
        *,
        all_visible: bool,
        app_name: str | None = None,
        focused_window_only: bool = False,
        anchor_hwnd: int = 0,
        anchor_pid: int = 0,
    ) -> Any | None:
        from .ax_models import AXCaptureResult

        _com_init()
        try:
            data = _build_payload(
                all_visible=all_visible,
                app_name=app_name,
                focused_window_only=focused_window_only,
                anchor_hwnd=anchor_hwnd,
                anchor_pid=anchor_pid,
                depth=self._depth,
                raw=self._raw,
            )
        finally:
            _com_fini()

        data = _strip_frame_fields(data)
        mode = "all-visible" if all_visible else "frontmost"
        return AXCaptureResult(
            raw_json=data,
            timestamp=data.get("timestamp", ""),
            apps=data.get("apps", []),
            metadata={
                "mode": mode,
                "depth": self._depth,
                "platform": "windows",
                "raw": self._raw,
                "engine": "pywinauto",
            },
        )
