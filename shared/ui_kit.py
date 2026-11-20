"""Shared workspace design: Module 2's design is the baseline for every module.

One place for the design tokens, the bundled Inter font, line icons, the placeholder logo,
plotly.js and the base components every module page is built from: the app sidebar
navigation, top bar, page head, cards, buttons, notices, pills, filter pills, help pop-overs,
tables, disclosures and the user block (with the demo account's portrait). Styles live in
``shared/assets/ui_kit.css`` and are scoped under ``.ws``; the tokens are also read by Module 2's
own stylesheet, so a token change reaches every module.

Nothing here reads data. ``shared/assets/ui_kit.js`` adds only the generic behaviour of filter
pills and help pop-overs (one open at a time, close on a click outside or Escape, the pill shows
its input's current value).
"""

from pathlib import Path

from htmltools import HTML, HTMLDependency, tags

from shared.chart_palette import CHART_COLORS, CHART_INK, CHART_NEUTRAL  # noqa: F401 (re-exported)

ASSETS = Path(__file__).resolve().parent / "assets"
VERSION = "1.0"

# Simple line icons (24x24, stroke = currentColor), drawn for this workspace.
ICON_PATHS = {
    "home": '<path d="M4 11l8-7 8 7"/><path d="M6 10v10h12V10"/>',
    "list": '<path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4.5" cy="6" r="1"/><circle cx="4.5" cy="12" r="1"/>'
            '<circle cx="4.5" cy="18" r="1"/>',
    "chart": '<path d="M4 20V4"/><path d="M4 20h16"/><path d="M8 16v-5M12 16V8M16 16v-8"/>',
    "pulse": '<path d="M3 12h4l2-6 4 12 2-6h6"/>',
    "gauge": '<path d="M4 16a8 8 0 1 1 16 0"/><path d="M12 16l4-5"/>',
    "help": '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6V14"/>'
            '<circle cx="12" cy="17.5" r=".6"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3'
                'l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1"/>',
    "logout": '<path d="M14 4h5v16h-5"/><path d="M10 8l-4 4 4 4"/><path d="M6 12h10"/>',
    "search": '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/>',
    "spark": '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/><path d="M19 15l.8 2.2L22 18l-2.2.8'
             'L19 21l-.8-2.2L16 18l2.2-.8z"/>',
    "mail": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/>',
    "bell": '<path d="M6 16V11a6 6 0 1 1 12 0v5l2 2H4z"/><path d="M10 20a2 2 0 0 0 4 0"/>',
    "calendar": '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
    "back": '<path d="M15 6l-6 6 6 6"/>',
    "bars": '<rect x="4" y="12" width="4" height="8" rx="1"/><rect x="10" y="7" width="4" height="13" rx="1"/>'
            '<rect x="16" y="3" width="4" height="17" rx="1"/>',
    "shelf": '<path d="M4 4v16M20 4v16M4 9h16M4 15h16"/><path d="M8 9V6.5h3V9M14 15v-3h3v3"/>',
    "scale": '<path d="M12 4v16M8 20h8M5 7h14"/><path d="M5 7l-3 6a3 3 0 0 0 6 0zM19 7l-3 6a3 3 0 0 0 6 0z"/>',
    "chat": '<path d="M20 15a2 2 0 0 1-2 2H8l-4 4V6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2z"/><path d="M8 9h8M8 13h5"/>',
    "send": '<path d="M4 12l16-8-6 16-2.5-6.5z"/><path d="M11.5 13.5L20 4"/>',
    "database": '<ellipse cx="12" cy="6" rx="7" ry="3"/><path d="M5 6v12c0 1.7 3.1 3 7 3s7-1.3 7-3V6"/>'
                '<path d="M5 12c0 1.7 3.1 3 7 3s7-1.3 7-3"/>',
    "doc": '<path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5M10 13h6M10 17h6"/>',
}


def icon(name, size=18, class_="ws-icon"):
    return HTML(f'<svg class="{class_}" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
                f'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" '
                f'aria-hidden="true" focusable="false">{ICON_PATHS[name]}</svg>')


def logo(class_="ws-logo-mark"):
    """Original placeholder mark for Marvel CPG (not a third-party logo): a red tile with a white
    shelf-and-peak monogram. To be replaced by the client's brand assets."""
    return HTML(
        f'<svg class="{class_}" width="32" height="32" viewBox="0 0 32 32" role="img" '
        'aria-label="Marvel CPG placeholder mark">'
        '<rect width="32" height="32" rx="8" fill="#b41824"/>'
        '<path d="M8 21V11.5l4.6 5.2L16 12l3.4 4.7 4.6-5.2V21" fill="none" stroke="#fff" stroke-width="2.6" '
        'stroke-linecap="round" stroke-linejoin="round"/>'
        '<path d="M8 24.5h16" stroke="#fff" stroke-width="1.8" stroke-linecap="round" opacity=".7"/></svg>')


def kit_dependency():
    """Inter 4.1 (SIL OFL 1.1, bundled; registered as "Workspace Inter"), the tokens and the
    ``.ws`` components. Every module page includes it; HTML dependencies load once."""
    return HTMLDependency("ws-kit", VERSION, source={"subdir": str(ASSETS)}, all_files=True,
                          stylesheet=[{"href": "fonts/inter.css"}, {"href": "ui_kit.css"}],
                          script={"src": "ui_kit.js"})


AVATAR_URL = f"lib/ws-kit-{VERSION}/avatars/"
_PHOTO_TYPES = (".jpg", ".jpeg", ".png", ".webp")


def avatar_url(key):
    """The demo account's portrait: a photo in assets/avatars when one is present, else its illustration."""
    if not key:
        return None
    for suffix in _PHOTO_TYPES:
        if (ASSETS / "avatars" / f"{key}{suffix}").is_file():
            return AVATAR_URL + key + suffix
    return AVATAR_URL + key + ".svg" if (ASSETS / "avatars" / f"{key}.svg").is_file() else None


def plotly_dependency():
    """Serve plotly.min.js from the installed plotly package (no CDN); loads once per page."""
    import plotly
    source = Path(plotly.__file__).parent / "package_data"
    return HTMLDependency("ws-plotly", plotly.__version__, source={"subdir": str(source)},
                          script={"src": "plotly.min.js"})


FONT_STACK = "Workspace Inter, Inter, -apple-system, Segoe UI, sans-serif"
INK = CHART_INK


def chart_layout(fig, height=300):
    """Module 2's Plotly look (font, ink, transparent paper, quiet grid)."""
    fig.update_layout(template="plotly_white", height=height,
                      font=dict(family=FONT_STACK, size=12, color=INK), paper_bgcolor="rgba(0,0,0,0)")
    return fig


# ------------------------------------------------------------------ components

def initials(name):
    parts = [p for p in "".join(c if c.isalnum() or c == " " else " " for c in (name or "")).split() if p]
    return ("".join(p[0] for p in parts[:2]) or "?").upper()


def avatar(name, src=None, size=36):
    """The portrait (``src``), or the name's initials without one."""
    if src:
        return tags.img(src=src, alt="", width=size, height=size, class_="ws-avatar photo", **{"aria-hidden": "true"})
    return tags.span(initials(name), class_="ws-avatar", **{"aria-hidden": "true"})


def user_block(name, title, class_="ws-user", avatar_src=None, **attrs):
    return tags.div(avatar(name, avatar_src),
                    tags.div(tags.strong(name), tags.span(title), class_="ws-user-text"), class_=class_, **attrs)


def nav_item(icon_name, label, *, active=False, disabled=False, note=None, caption=None, **attrs):
    """A sidebar navigation button. ``disabled``: visible and focusable, announced as not available."""
    tag = tags.span(note, class_="ws-soon") if note else None
    # With a caption, the note sits on the caption line so the label keeps the full width.
    text = tags.span(tags.span(label, class_="ws-nav-text"),
                     tags.span(tags.small(caption, class_="ws-nav-caption"), tag, class_="ws-nav-sub")
                     if caption else None, class_="ws-nav-body")
    extra = {"aria-disabled": "true"} if disabled else ({"aria-current": "page"} if active else {})
    return tags.button(icon(icon_name), text, None if caption else tag, type="button",
                       class_="ws-nav-item" + (" active" if active else ""), **extra, **attrs)


def placeholder_button(icon_name, label, text=None):
    """A top-bar control that is visible and focusable but announced as not available (no handler)."""
    return tags.button(icon(icon_name), tags.span(text, class_="ws-ph-text") if text else None,
                       tags.span("Soon", class_="ws-soon") if text else None, type="button",
                       class_="ws-icon-btn" + (" ws-copilot" if text else ""), title=f"{label} — coming soon",
                       **{"aria-disabled": "true", "aria-label": f"{label} — coming soon"})


def filter_pill(pill_id, title, face, *body, icon_name=None, class_="", pop_class="", **attrs):
    """One filter pill (Module 1's pattern for every module): the face shows the current value; the options
    open below it. ``face``: markup, or ``ws-fpill-val`` spans with ``data-for`` naming an input whose
    current choice ui_kit.js writes into them."""
    return tags.details(
        tags.summary(icon(icon_name, 15) if icon_name else None, face,
                     tags.span("▾", class_="ws-fpill-caret", **{"aria-hidden": "true"}),
                     class_="ws-fpill-face", **{"aria-label": f"{title} filter"}),
        tags.div(tags.h3(title, class_="ws-fpop-title"), *body, class_=f"ws-fpop {pop_class}".strip(), role="group",
                 **{"aria-label": title}),
        class_=f"ws-fpill {class_}".strip(), id=pill_id, **attrs)


def pill_face(key, input_id, empty="All", unit=None, narrows=True):
    """A filter pill's face: its name and the current value of ``input_id`` (kept current by ui_kit.js).
    ``narrows``: a chosen value narrows the data (highlighted, as in Module 1); False for a plain selection."""
    return tags.span(tags.span(key, class_="ws-fpill-key"),
                     tags.span(empty, class_="ws-fpill-val", **{"data-for": input_id, "data-empty": empty,
                                                                **({"data-unit": unit} if unit else {}),
                                                                **({} if narrows else {"data-plain": ""})}),
                     class_="ws-fpill-text")


def help_tip(title, *body, id=None, align="left"):
    """A "?" button next to a title that opens a short note on how to read it (closed by default)."""
    return tags.details(
        tags.summary(icon("help", 16), tags.span(f"How to read: {title}", class_="ws-sr"), class_="ws-help-btn",
                     title="How to read this"),
        tags.div(tags.p(tags.strong(title), class_="ws-help-title"), *body, class_=f"ws-help-pop {align}"),
        class_="ws-help", id=id)


def topbar_actions(user, *, copilot_id=None, copilot=True):
    """Module 2's top-bar actions for every module: Copilot, messages, notifications, calendar, the user.

    ``copilot_id``: input id of a working Copilot button (the module opens the host's copilot on it);
    without one the button is a placeholder. ``copilot=False`` leaves it out (the copilot's own page).
    """
    if not copilot:
        button = None
    elif copilot_id:
        button = tags.button(icon("spark"), tags.span("Copilot", class_="ws-ph-text"), type="button", id=copilot_id,
                             class_="action-button ws-icon-btn ws-copilot", title="Open the Commercial Copilot")
    else:
        button = placeholder_button("spark", "Copilot", "Copilot")
    return (button, placeholder_button("mail", "Messages"), placeholder_button("bell", "Notifications"),
            placeholder_button("calendar", "Calendar"),
            tags.span(class_="ws-topbar-sep", **{"aria-hidden": "true"}), user)


def topbar(crumbs, *actions, search=None):
    """``crumbs``: (module name, page name). Placeholder-free: only the given actions are shown."""
    module, page = crumbs
    crumb = tags.nav(tags.span(module), tags.span("/", **{"aria-hidden": "true"}), tags.strong(page),
                     class_="ws-crumb", **{"aria-label": "Breadcrumb"})
    return tags.div(tags.div(crumb, class_="ws-crumb-wrap"), search,
                    tags.div(*actions, class_="ws-topbar-actions"), class_="ws-topbar")


def page_head(title, *meta, action=None, sub=None):
    return tags.div(tags.div(tags.h1(title, class_="ws-title"), tags.p(sub, class_="ws-sub") if sub else None,
                             tags.p(*meta, class_="ws-meta") if meta else None),
                    action, class_="ws-pagehead")


def card(*children, class_="", **attrs):
    return tags.div(*children, class_=("ws-card " + class_).strip(), **attrs)


def notice(*children, kind="info", **attrs):
    """Plain-language status line: info (neutral), warn or bad. Text always carries the meaning."""
    return tags.div(*children, class_=f"ws-banner {kind}", **attrs)


def pill(text, kind="plain", dot=None, class_="", **attrs):
    """Status pill; ``dot`` ("ok" / "bad") adds a coloured marker next to the text (the text carries the meaning)."""
    marker = tags.span(class_=f"ws-dot {dot}", **{"aria-hidden": "true"}) if dot else None
    return tags.span(marker, text, class_=f"ws-pill {kind} {class_}".strip(), **attrs)


__all__ = ["CHART_COLORS", "FONT_STACK", "ICON_PATHS", "avatar", "avatar_url", "card", "chart_layout", "filter_pill",
           "help_tip", "icon", "initials", "kit_dependency", "logo", "nav_item", "notice", "page_head", "pill",
           "pill_face", "plotly_dependency", "topbar", "user_block"]
