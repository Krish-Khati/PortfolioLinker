"""
post -> Blogger-ready HTML.

Blogger renders the title itself, so the body starts at the lede. Links are
placed by swapping the [[LINK1]] / [[LINK2]] tokens the generator emitted for
real <a> tags, which means link placement is inherited from validated text
rather than re-derived here.
"""

from __future__ import annotations

import html
import re
from typing import Any

import profile as P

LINK_TOKEN = re.compile(r"\[\[LINK(\d)\]\]")

# Only these attributes ever reach the output, and targets are forced through
# the verified LINK_TARGETS table so a model can never inject a URL.
ALLOWED_ATTRS = {"href", "title", "rel", "target"}


def _esc(text: Any) -> str:
    """Escape for HTML. Coerces non-strings so a stray list cannot crash a render."""
    if text is None:
        return ""
    if not isinstance(text, str):
        text = ", ".join(str(t) for t in text) if isinstance(text, (list, tuple)) \
            else str(text)
    return html.escape(text, quote=True)


def resolve_links(post: dict[str, Any]) -> dict[str, str]:
    """token -> (href, anchor). Raises if a target is not in the allow-list."""
    out: dict[str, str] = {}
    for link in post.get("backlinks", []) or []:
        target = link.get("target")
        if target not in P.LINK_TARGETS:
            raise ValueError(f"refusing to render unverified link target {target!r}")
        anchor = (link.get("anchor") or "").strip()
        if not anchor:
            raise ValueError(f"empty anchor for {target!r}")
        if link.get("id") in out:
            raise ValueError(f"duplicate link id {link.get('id')!r}")
        out[link["id"]] = (P.LINK_TARGETS[target], anchor)
    return out


def _sub_link(match: re.Match, links: dict[str, tuple[str, str]]) -> str:
    entry = links.get(f"LINK{match.group(1)}")
    if not entry:
        return ""
    href, anchor = entry
    return (
        f'<a href="{_esc(href)}" rel="noopener" '
        f'title="{_esc(anchor)}">{_esc(anchor)}</a>'
    )


def to_html(post: dict[str, Any], image_url: str, image: dict[str, Any] | None) -> str:
    """Render the post body.

    image_url is whatever should go in the <img src>. For a published post
    that is a self-contained data URI (see publish_blogger._image_data_uri),
    for a local preview it is the path of the file on disk.
    """
    links = resolve_links(post)

    parts: list[str] = []

    lede = post.get("lede", "").strip()
    if lede:
        parts.append(f'<p class="lede">{LINK_TOKEN.sub(lambda m: _sub_link(m, links), _esc(lede))}</p>')

    if image_url:
        alt = _esc(post.get("alt_text", ""))
        parts.append(
            f'<figure class="post-figure">'
            f'<img src="{_esc(image_url)}" alt="{alt}" '
            f'loading="lazy" decoding="async">'
            f"</figure>"
        )

    for section in post.get("sections", []) or []:
        heading = section.get("heading", "").strip()
        if heading:
            parts.append(f"<h2>{_esc(heading)}</h2>")
        for para in section.get("paragraphs", []) or []:
            body = LINK_TOKEN.sub(lambda m: _sub_link(m, links), _esc(para))
            parts.append(f"<p>{body}</p>")

    return "\n".join(parts)


def to_html_with_local_image(post: dict[str, Any], local_path: str,
                             image: dict[str, Any] | None) -> str:
    """Same as to_html but with a local path, for the drafts/ preview only."""
    return to_html(post, local_path, image)


def link_inventory(body_html: str) -> list[str]:
    """Every href in the rendered body, for the gate and for eyeballing."""
    return re.findall(r'<a\b[^>]*href="([^"]+)"', body_html)


def audit(body_html: str) -> dict[str, Any]:
    """Post-render checks that the text-level validator cannot make."""
    hrefs = link_inventory(body_html)
    allowed = set(P.LINK_TARGETS.values())
    return {
        "link_count": len(hrefs),
        "distinct_targets": len(set(hrefs)),
        "unknown_targets": [h for h in hrefs if h not in allowed],
        "has_img": "<img " in body_html,
        "img_has_alt": bool(re.search(r"<img\b[^>]*\balt=\"[^\"]+\"", body_html)),
        "residual_tokens": LINK_TOKEN.findall(body_html),
        "h2_count": len(re.findall(r"<h2>", body_html)),
        "paragraph_count": len(re.findall(r"<p[ >]", body_html)),
    }


def preview_page(post: dict[str, Any], image_path: str,
                 image: dict[str, Any] | None, gate_text: str = "") -> str:
    """A standalone HTML file you can open in a browser to eyeball the draft."""
    body = to_html_with_local_image(post, image_path, image)
    a = audit(body)
    rel_img = image_path.replace("\\", "/").split("/")[-1]
    body = body.replace(image_path, rel_img)

    gate_html = ""
    if gate_text:
        gate_html = (
            f'<pre style="background:#111;color:#eee;padding:16px;'
            f'white-space:pre-wrap;font-size:13px;border-radius:8px;'
            f'overflow:auto">{_esc(gate_text)}</pre>'
        )

    audit_html = (
        f'<pre style="background:#0b0;font-size:12px;padding:10px;'
        f'border-radius:6px">{_esc(json_pretty(a))}</pre>'
    )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(post.get('title', ''))}</title>
<meta name="description" content="{_esc(post.get('meta_description', ''))}">
<meta name="keywords" content="{_esc(', '.join(post.get('tags', [])))}">
<meta name="robots" content="noindex">
<style>
 body{{font:17px/1.75 system-ui,sans-serif;max-width:740px;margin:40px auto;
      padding:0 20px;color:#222;background:#fdfdfd}}
 h1{{font-size:2em;line-height:1.2;margin:0 0 .2em}}
 h2{{margin-top:1.8em;border-left:4px solid #16a085;padding-left:12px}}
 .lede{{font-size:1.1em;color:#444}}
 .post-figure img{{width:100%;border-radius:10px;display:block}}
 a{{color:#0a7}}
 .meta{{color:#777;font-size:13px;border-bottom:1px solid #ddd;padding-bottom:12px}}
</style></head><body>
<h1>{_esc(post.get('title', ''))}</h1>
<p class="meta">slug: {_esc(post.get('slug', ''))} &middot;
 {post.get('word_count', '?')} words &middot;
 tags: {_esc(', '.join(post.get('tags', [])))}</p>
{body}
<h2 style="border-color:#999">Render audit</h2>
{audit_html}
{gate_html}
</body></html>"""


def json_pretty(obj: Any) -> str:
    import json

    return json.dumps(obj, indent=2, ensure_ascii=False)