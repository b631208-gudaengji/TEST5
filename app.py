import html
import json
import time
import uuid

import streamlit as st
from streamlit_js_eval import streamlit_js_eval

STORAGE_KEY = "shopping_list_v1"
LOAD_TIMEOUT_SEC = 5  # localStorage 응답을 기다리는 최대 시간
ERR_SENTINEL = "__ERR__"

st.set_page_config(page_title="쇼핑 리스트", page_icon="🛒", layout="centered")


# ---------- 상태 ----------
def init_state():
    """session_state 초기화 (PRD 6장 데이터 모델)."""
    st.session_state.setdefault("items", [])  # 각 항목: {"id": int, "text": str, "checked": bool}
    st.session_state.setdefault("next_id", 1)
    st.session_state.setdefault("editing_id", None)
    st.session_state.setdefault("tab_id", uuid.uuid4().hex)  # 탭 구분용 (자기 쓰기 무시)
    st.session_state.setdefault("storage_ok", True)
    st.session_state.setdefault("synced_ts", 0)


def find_item(item_id):
    for item in st.session_state["items"]:
        if item["id"] == item_id:
            return item
    return None


def add_item(text: str):
    """항목을 목록 맨 아래에 추가하고 id를 발급한다."""
    st.session_state["items"].append(
        {"id": st.session_state["next_id"], "text": text, "checked": False}
    )
    st.session_state["next_id"] += 1


def delete_item(item_id):
    st.session_state["items"] = [i for i in st.session_state["items"] if i["id"] != item_id]
    if st.session_state["editing_id"] == item_id:
        st.session_state["editing_id"] = None


def delete_checked():
    st.session_state["items"] = [i for i in st.session_state["items"] if not i["checked"]]
    # 수정 중이던 항목이 사라졌다면 수정 모드 해제
    if find_item(st.session_state["editing_id"]) is None:
        st.session_state["editing_id"] = None


# ---------- 브라우저 저장 (localStorage) ----------
def _content() -> str:
    """저장 대상 내용만 직렬화한 문자열 (변경 감지용)."""
    return json.dumps(
        {"items": st.session_state["items"], "next_id": st.session_state["next_id"]},
        ensure_ascii=False,
        sort_keys=True,
    )


def _parse(raw: str):
    """저장된 JSON 문자열을 파싱한다. 깨진 데이터면 None."""
    try:
        data = json.loads(raw)
        items = [
            {"id": int(i["id"]), "text": str(i["text"]), "checked": bool(i["checked"])}
            for i in data["items"]
        ]
        max_id = max((i["id"] for i in items), default=0)
        next_id = max(int(data.get("next_id", 1)), max_id + 1)
        return data, items, next_id
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def _apply(items, next_id, ts):
    """다른 곳에서 읽은 목록을 현재 세션 상태에 반영한다."""
    st.session_state["items"] = items
    st.session_state["next_id"] = next_id
    st.session_state["synced_content"] = _content()
    st.session_state["synced_ts"] = ts
    # 위젯 상태가 이전 목록 기준으로 남아 있으면 새 값이 무시되므로 비운다
    for k in [k for k in st.session_state if k.startswith(("chk_", "edit_"))]:
        if k not in ("edit_error",):
            del st.session_state[k]
    st.session_state.pop("edit_error", None)
    if find_item(st.session_state["editing_id"]) is None:
        st.session_state["editing_id"] = None


# localStorage가 막혀 있으면(시크릿 모드 등) 예외 대신 센티널을 돌려준다
LOAD_JS = (
    "(() => { try { return localStorage.getItem('%s') || ''; } "
    "catch (e) { return '%s'; } })()" % (STORAGE_KEY, ERR_SENTINEL)
)

# 다른 탭이 저장하면 storage 이벤트로 새 값을 Python에 전달한다
WATCH_JS = (
    "(() => { if (!window.__shoppingWatch) { window.__shoppingWatch = true; "
    "window.addEventListener('storage', (e) => { "
    "if (e.key === '%s' && e.newValue) sendDataToPython({value: e.newValue, dataType: 'json'}); "
    "}); } return null; })()" % STORAGE_KEY
)


@st.fragment(run_every=1)
def loading_watchdog():
    """응답이 영영 오지 않을 때를 대비해 주기적으로 전체 앱을 다시 실행한다."""
    if time.time() - st.session_state["load_started"] > LOAD_TIMEOUT_SEC:
        st.rerun()


def load_from_browser() -> bool:
    """브라우저 localStorage에서 목록을 불러온다. 불러오기가 끝났으면 True.

    컴포넌트는 첫 실행에서 None을 돌려주고, 브라우저가 응답한 다음 실행에서 값을 돌려준다.
    저장된 값이 없으면 빈 문자열이 온다 (None과 구분하기 위해 || '' 사용).
    """
    if st.session_state.get("loaded"):
        return True
    st.session_state.setdefault("load_started", time.time())
    raw = streamlit_js_eval(js_expressions=LOAD_JS, key="ls_load")

    if raw is None:
        if time.time() - st.session_state["load_started"] > LOAD_TIMEOUT_SEC:
            st.session_state["storage_ok"] = False  # 응답 없음: 저장 없이 진행
        else:
            return False
    elif raw == ERR_SENTINEL:
        st.session_state["storage_ok"] = False
    elif raw:
        parsed = _parse(raw)
        if parsed:
            data, items, next_id = parsed
            _apply(items, next_id, data.get("ts", 0))
    st.session_state["loaded"] = True
    st.session_state.setdefault("synced_content", _content())
    return True


def sync_from_other_tabs():
    """다른 탭이 저장한 내용을 반영한다 (오래된 탭이 새 데이터를 덮어쓰는 것을 방지)."""
    raw = streamlit_js_eval(js_expressions=WATCH_JS, key="ls_watch")
    if not raw or raw == st.session_state.get("last_remote"):
        return
    st.session_state["last_remote"] = raw
    parsed = _parse(raw)
    if not parsed:
        return
    data, items, next_id = parsed
    if data.get("writer") == st.session_state["tab_id"]:
        return  # 내가 쓴 것
    if data.get("ts", 0) <= st.session_state["synced_ts"]:
        return  # 이미 반영한 것보다 오래된 데이터
    _apply(items, next_id, data["ts"])


def save_to_browser():
    """내용이 바뀐 실행에서만 새 payload를 만든다.

    저장 컴포넌트는 식(JS 문자열)이 바뀔 때만 다시 실행된다. 같은 payload를 계속 렌더링해도
    저장소의 ts가 더 새로우면 JS에서 덮어쓰지 않으므로 안전하다.
    """
    content = _content()
    if content != st.session_state.get("synced_content"):
        ts = max(time.time_ns() // 1_000_000, st.session_state["synced_ts"] + 1)
        payload = {
            "ts": ts,
            "writer": st.session_state["tab_id"],
            "items": st.session_state["items"],
            "next_id": st.session_state["next_id"],
        }
        st.session_state["last_payload"] = json.dumps(payload, ensure_ascii=False)
        st.session_state["synced_content"] = content
        st.session_state["synced_ts"] = ts
    payload_str = st.session_state.get("last_payload")
    if not payload_str:
        return
    js = (
        "(() => { try { const raw = %s; const nxt = JSON.parse(raw); "
        "const cur = JSON.parse(localStorage.getItem('%s') || 'null'); "
        "if (!cur || (cur.ts || 0) <= nxt.ts) localStorage.setItem('%s', raw); "
        "} catch (e) {} })()" % (json.dumps(payload_str), STORAGE_KEY, STORAGE_KEY)
    )
    streamlit_js_eval(js_expressions=js, want_output=False, key="ls_save")


# ---------- 콜백 (버튼/체크박스 이벤트) ----------
def on_toggle(item_id):
    item = find_item(item_id)
    if item is not None:
        item["checked"] = st.session_state[f"chk_{item_id}"]


def on_edit(item_id):
    st.session_state["editing_id"] = item_id
    st.session_state.pop("edit_error", None)


def on_cancel():
    st.session_state["editing_id"] = None
    st.session_state.pop("edit_error", None)


def on_save(item_id):
    new_text = st.session_state.get(f"edit_{item_id}", "").strip()
    if not new_text:
        st.session_state["edit_error"] = "항목 이름은 비워둘 수 없어요."
        return
    item = find_item(item_id)
    if item is not None:
        item["text"] = new_text
    st.session_state["editing_id"] = None
    st.session_state.pop("edit_error", None)


# ---------- 화면 ----------
CSS = """
<style>
/* 좁은 화면에서도 행(체크박스/이름/수정/삭제)이 세로로 쌓이지 않도록 고정 */
[data-testid="stHorizontalBlock"] { flex-wrap: nowrap !important; align-items: center; gap: 0.5rem; }
[data-testid="stHorizontalBlock"] > [data-testid="stColumn"] { min-width: 0 !important; }
[data-testid="stBaseButton-primary"] p, [data-testid="stBaseButton-secondary"] p { white-space: nowrap; }
/* 모바일 터치 영역 (권장 44px) */
[data-testid="stBaseButton-tertiary"] { min-height: 44px; min-width: 44px; }
[data-testid="stCheckbox"] label { min-height: 44px; min-width: 44px; align-items: center; }
/* 화면에 보이지 않는 저장/동기화 컴포넌트가 빈 간격을 만들지 않게 한다 (스크립트는 계속 동작) */
[data-testid="stElementContainer"]:has(iframe[title*="streamlit_js_eval"]) {
  position: absolute; width: 0; height: 0; overflow: hidden; margin: 0; padding: 0;
}
.item-name { overflow-wrap: anywhere; line-height: 1.4; position: relative; top: 6px; }
.item-name.done { text-decoration: line-through; color: #9aa5a0; }
.block-container { padding-top: 2.5rem; max-width: 640px; }
</style>
"""


def render_header():
    st.markdown(CSS, unsafe_allow_html=True)
    st.title("🛒 쇼핑 리스트")
    st.caption("오늘 살 것을 적어보세요")
    if not st.session_state["storage_ok"]:
        st.warning("이 브라우저에서는 목록을 저장할 수 없어요. 새로고침하면 목록이 사라져요.")


def render_add_form():
    with st.form("add_form", clear_on_submit=True):
        c_in, c_btn = st.columns([5, 1.4])
        text = c_in.text_input("아이템", placeholder="살 물건을 입력하세요", label_visibility="collapsed")
        submitted = c_btn.form_submit_button("추가", type="primary", use_container_width=True)
    if submitted:
        text = text.strip()
        if not text:
            st.warning("항목 이름을 입력해 주세요.")
        else:
            add_item(text)
            st.rerun()


def render_progress():
    items = st.session_state["items"]
    total = len(items)
    done = sum(1 for i in items if i["checked"])
    st.progress(done / total if total else 0.0, text=f"전체 {total}개 중 {done}개 완료")


def render_item_row(item):
    item_id = item["id"]
    if st.session_state["editing_id"] == item_id:
        # 수정 모드: form으로 묶어 Enter로도 저장되게 한다
        key = f"edit_{item_id}"
        if key not in st.session_state:
            st.session_state[key] = item["text"]  # 최초 진입 시에만 현재 이름으로 채움
        with st.form(f"edit_form_{item_id}", border=False):
            c_input, c_save, c_cancel = st.columns([5, 2.2, 2.2])
            c_input.text_input("이름 수정", key=key, label_visibility="collapsed")
            c_save.form_submit_button("저장", type="primary", use_container_width=True,
                                      on_click=on_save, args=(item_id,))
            c_cancel.form_submit_button("취소", use_container_width=True, on_click=on_cancel)
        if st.session_state.get("edit_error"):
            st.warning(st.session_state["edit_error"])
    else:
        c_chk, c_name, c_edit, c_del = st.columns([1.6, 6.7, 1.5, 1.5])
        c_chk.checkbox(
            "완료 체크",
            value=item["checked"],
            key=f"chk_{item_id}",
            on_change=on_toggle,
            args=(item_id,),
            label_visibility="collapsed",
        )
        # 이름은 마크다운이 아니라 이스케이프한 HTML 텍스트로 표시 (URL/&/: 변환 방지)
        cls = "item-name done" if item["checked"] else "item-name"
        c_name.html(f'<div class="{cls}">{html.escape(item["text"])}</div>')
        c_edit.button("✏️", key=f"edit_btn_{item_id}", on_click=on_edit, args=(item_id,),
                      help="수정", type="tertiary", use_container_width=True)
        c_del.button("🗑️", key=f"del_{item_id}", on_click=delete_item, args=(item_id,),
                     help="삭제", type="tertiary", use_container_width=True)


def render_list():
    items = st.session_state["items"]
    if not items:
        st.markdown(
            "<div style='text-align:center;padding:2.5rem 0;color:#7a8a80'>"
            "<div style='font-size:3rem'>🧺</div>"
            "<div>아직 항목이 없어요<br>위에서 살 물건을 추가해 보세요</div></div>",
            unsafe_allow_html=True,
        )
        return
    render_progress()
    for item in items:
        render_item_row(item)
    has_checked = any(i["checked"] for i in items)
    st.divider()
    if st.button("체크한 항목 모두 삭제", disabled=not has_checked, key="del_checked", type="tertiary"):
        delete_checked()
        st.rerun()


def main():
    init_state()
    # 불러오기 전에 저장하면 기존 데이터를 빈 목록으로 덮어쓰므로, 로딩이 끝날 때까지 대기
    if not load_from_browser():
        render_header()
        st.caption("목록을 불러오는 중...")
        loading_watchdog()
        return
    if st.session_state["storage_ok"]:
        sync_from_other_tabs()
    render_header()
    render_add_form()
    render_list()
    if st.session_state["storage_ok"]:
        save_to_browser()


main()
