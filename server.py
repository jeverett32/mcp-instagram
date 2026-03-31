#!/usr/bin/env python3
"""
Instagram MCP Server - Fetch public Instagram profiles, posts, and images
via the mobile API using curl_cffi for TLS fingerprint bypass.

Tools:
  - get_profile: Profile info (bio, followers, category, profile pic)
  - get_posts: Recent posts with captions, stats, and carousel support
  - get_post_images: Flat list of HD image URLs (skips videos)
"""

from datetime import datetime, timezone
from typing import Any, Optional
from mcp.server.fastmcp import FastMCP
from curl_cffi import requests
import json
from urllib.parse import quote
import os

mcp = FastMCP("instagram")

IG_APP_ID = "936619743392459"
IG_USER_AGENT = (
    "Instagram 275.0.0.27.98 Android "
    "(33/13; 420dpi; 1080x2400; samsung; SM-G991B; o1s; exynos2100; en_US; 458229258)"
)
IG_HEADERS = {
    "User-Agent": IG_USER_AGENT,
    "X-IG-App-ID": IG_APP_ID,
    "X-IG-Device-ID": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "X-Requested-With": "XMLHttpRequest",
}

# Optional auth: for endpoints that require login (pagination, some feeds).
# Provide via MCP server env var IG_SESSIONID.
IG_SESSIONID = os.getenv("IG_SESSIONID", "").strip()


def _auth_headers() -> dict[str, str]:
    headers = dict(IG_HEADERS)
    if IG_SESSIONID:
        # Instagram expects sessionid cookie when authenticated.
        headers["Cookie"] = f"sessionid={IG_SESSIONID}"
    return headers


def _clean_username(username: str) -> str:
    """Extract clean username from various input formats."""
    username = username.strip().lstrip("@").rstrip("/")
    if "instagram.com" in username:
        username = username.split("instagram.com/")[-1].split("/")[0].split("?")[0]
    return username


def _fetch_user_data(username: str) -> dict:
    """Fetch raw user data from Instagram API."""
    r = requests.get(
        f"https://i.instagram.com/api/v1/users/web_profile_info/?username={username}",
        headers=_auth_headers(),
        impersonate="chrome",
        timeout=10,
    )
    if r.status_code == 404:
        raise ValueError(f"User '{username}' not found")
    if r.status_code != 200:
        raise RuntimeError(f"Instagram API returned HTTP {r.status_code}")
    data = r.json()
    user = data.get("data", {}).get("user")
    if not user:
        raise ValueError(f"No data returned for '{username}'")
    return user


def _ts_to_iso(ts: int) -> str:
    """Convert Unix timestamp to ISO date string."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _best_candidate_url(candidates: list[dict[str, Any]] | None) -> str:
    if not candidates:
        return ""
    # Prefer the first (often highest res) candidate.
    return candidates[0].get("url", "") or ""


def _graphql_node_to_post(node: dict[str, Any]) -> dict[str, Any]:
    captions = node.get("edge_media_to_caption", {}).get("edges", [])
    caption = captions[0]["node"]["text"] if captions else ""

    post: dict[str, Any] = {
        "id": node.get("shortcode", ""),
        "type": node.get("__typename", ""),
        "image_url": node.get("display_url", ""),
        "thumbnail_url": node.get("thumbnail_src", "") or node.get("display_url", ""),
        "caption": caption,
        "likes": node.get("edge_liked_by", {}).get("count", 0),
        "comments": node.get("edge_media_to_comment", {}).get("count", 0),
        "date": _ts_to_iso(node["taken_at_timestamp"]) if "taken_at_timestamp" in node else "",
        "is_video": node.get("is_video", False),
        # GraphQL timeline nodes often omit a direct video_url; keep stable key.
        "video_url": node.get("video_url", "") or "",
    }

    sidecar = node.get("edge_sidecar_to_children", {}).get("edges", [])
    if sidecar:
        post["carousel"] = [
            {
                "image_url": child["node"].get("display_url", ""),
                "is_video": child["node"].get("is_video", False),
                "video_url": child["node"].get("video_url", "") or "",
            }
            for child in sidecar
        ]

    return post


def _fetch_graphql_timeline_page(
    user_id: str, after: Optional[str], first: int, username: str
) -> dict[str, Any]:
    """
    Fetch a page of posts using Instagram's web GraphQL timeline query.

    This avoids the authenticated mobile feed endpoint and supports cursor pagination
    via `page_info.end_cursor`.
    """
    # Query hash for profile timeline media. Instagram may rotate this over time.
    # If this starts returning HTTP 400/403, it likely needs updating.
    query_hash = "42323d64886122307be10013ad2dcc44"

    variables = {"id": str(user_id), "first": int(first)}
    if after:
        variables["after"] = after

    # Keep the URL encoding explicit so it works reliably on Windows.
    vars_enc = quote(json.dumps(variables, separators=(",", ":")))
    url = f"https://www.instagram.com/graphql/query/?query_hash={query_hash}&variables={vars_enc}"

    headers = _auth_headers()
    headers.update(
        {
            "Referer": f"https://www.instagram.com/{username}/",
            "Origin": "https://www.instagram.com",
        }
    )

    r = requests.get(
        url,
        headers=headers,
        impersonate="chrome",
        timeout=15,
    )
    if r.status_code == 401:
        raise RuntimeError("Instagram GraphQL returned HTTP 401 (blocked/needs auth)")
    if r.status_code != 200:
        raise RuntimeError(f"Instagram GraphQL returned HTTP {r.status_code}")
    return r.json()


def _fetch_user_feed_page_auth(user_id: str, max_id: Optional[str], count: int) -> dict[str, Any]:
    """
    Authenticated timeline paging. Requires IG_SESSIONID.
    """
    if not IG_SESSIONID:
        raise RuntimeError("IG_SESSIONID is not set; cannot use authenticated feed pagination")

    url = f"https://i.instagram.com/api/v1/feed/user/{user_id}/?count={count}"
    if max_id:
        url += f"&max_id={max_id}"

    r = requests.get(
        url,
        headers=_auth_headers(),
        impersonate="chrome",
        timeout=15,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Instagram feed API returned HTTP {r.status_code}")
    return r.json()


def _item_to_post(item: dict[str, Any]) -> dict[str, Any]:
    """
    Normalize an item from /feed/user/{user_id}/ into the same shape as get_posts().
    """
    caption_text = ""
    caption_obj = item.get("caption")
    if isinstance(caption_obj, dict):
        caption_text = caption_obj.get("text", "") or ""

    media_type = item.get("media_type")  # 1=image, 2=video, 8=carousel

    post: dict[str, Any] = {
        "id": item.get("code", "") or item.get("shortcode", ""),
        "type": {1: "GraphImage", 2: "GraphVideo", 8: "GraphSidecar"}.get(media_type, ""),
        "image_url": "",
        "thumbnail_url": "",
        "caption": caption_text,
        "likes": item.get("like_count", 0) or 0,
        "comments": item.get("comment_count", 0) or 0,
        "date": _ts_to_iso(item["taken_at"]) if "taken_at" in item else "",
        "is_video": bool(media_type == 2),
        "video_url": "",
    }

    if media_type == 2:
        post["video_url"] = _best_candidate_url(item.get("video_versions"))
        post["image_url"] = _best_candidate_url(
            (item.get("image_versions2") or {}).get("candidates")
        )
        post["thumbnail_url"] = post["image_url"]
        return post

    if media_type == 1:
        post["image_url"] = _best_candidate_url(
            (item.get("image_versions2") or {}).get("candidates")
        )
        post["thumbnail_url"] = post["image_url"]
        return post

    carousel_media = item.get("carousel_media") or []
    if isinstance(carousel_media, list) and carousel_media:
        children = []
        for child in carousel_media:
            child_type = child.get("media_type")
            child_entry = {
                "image_url": _best_candidate_url(
                    (child.get("image_versions2") or {}).get("candidates")
                ),
                "is_video": bool(child_type == 2),
                "video_url": _best_candidate_url(child.get("video_versions")) if child_type == 2 else "",
            }
            children.append(child_entry)

        post["carousel"] = children
        post["image_url"] = children[0].get("image_url", "") if children else ""
        post["thumbnail_url"] = post["image_url"]

    return post


@mcp.tool()
def get_profile(username: str) -> dict:
    """Get Instagram profile info: photo, bio, followers, category.
    Accepts a username or full Instagram URL."""
    username = _clean_username(username)
    user = _fetch_user_data(username)
    return {
        "username": user.get("username", username),
        "full_name": user.get("full_name", ""),
        "biography": user.get("biography", ""),
        "profile_pic_url": user.get("profile_pic_url", ""),
        "profile_pic_url_hd": user.get("profile_pic_url_hd", ""),
        "followers": user.get("edge_followed_by", {}).get("count", 0),
        "following": user.get("edge_follow", {}).get("count", 0),
        "posts_count": user.get("edge_owner_to_timeline_media", {}).get("count", 0),
        "is_business": user.get("is_business_account", False),
        "is_verified": user.get("is_verified", False),
        "category": user.get("category_name", ""),
        "external_url": user.get("external_url", ""),
    }


@mcp.tool()
def get_posts(username: str, limit: int = 12) -> dict:
    """Get recent Instagram posts with images, captions, and stats.
    Returns up to 12 posts (API limit per request).
    Accepts a username or full Instagram URL."""
    username = _clean_username(username)
    user = _fetch_user_data(username)
    media = user.get("edge_owner_to_timeline_media", {})
    edges = media.get("edges", [])[:limit]

    posts = []
    for edge in edges:
        node = edge["node"]
        captions = node.get("edge_media_to_caption", {}).get("edges", [])
        caption = captions[0]["node"]["text"] if captions else ""

        post = {
            "id": node.get("shortcode", ""),
            "type": node.get("__typename", ""),
            "image_url": node.get("display_url", ""),
            "thumbnail_url": node.get("thumbnail_src", ""),
            "caption": caption,
            "likes": node.get("edge_liked_by", {}).get("count", 0),
            "comments": node.get("edge_media_to_comment", {}).get("count", 0),
            "date": _ts_to_iso(node["taken_at_timestamp"]) if "taken_at_timestamp" in node else "",
            "is_video": node.get("is_video", False),
            "video_url": node.get("video_url", ""),
        }

        # Carousel/sidecar: include all child images
        sidecar = node.get("edge_sidecar_to_children", {}).get("edges", [])
        if sidecar:
            post["carousel"] = [
                {
                    "image_url": child["node"].get("display_url", ""),
                    "is_video": child["node"].get("is_video", False),
                    "video_url": child["node"].get("video_url", ""),
                }
                for child in sidecar
            ]

        posts.append(post)

    return {
        "username": username,
        "total_posts": media.get("count", 0),
        "returned": len(posts),
        "posts": posts,
    }


@mcp.tool()
def get_posts_paged(
    username: str,
    limit: int = 36,
    page_size: int = 12,
    max_id: str = "",
    max_pages: int = 10,
) -> dict:
    """
    Get recent Instagram posts with pagination support (beyond the ~12 shown in web_profile_info).

    - limit: total posts to return (best-effort; subject to Instagram/rate limits)
    - page_size: requested items per page (Instagram may cap/adjust)
    - max_id: cursor for fetching older posts (empty string = start from newest)
    - max_pages: safety cap on requests per call

    Returns:
    - next_max_id: cursor to request the next (older) page
    - more_available: whether Instagram indicates more pages exist
    """
    username = _clean_username(username)
    if limit <= 0:
        return {
            "username": username,
            "returned": 0,
            "posts": [],
            "next_max_id": "",
            "more_available": False,
        }

    user = _fetch_user_data(username)
    user_id = user.get("id") or user.get("pk") or ""
    if not user_id:
        raise RuntimeError("Could not determine Instagram user id for pagination")

    collected: list[dict[str, Any]] = []
    cursor: Optional[str] = max_id or None  # GraphQL cursor OR feed max_id
    more_available = True
    pages = 0

    while len(collected) < limit and more_available and pages < max_pages:
        pages += 1
        # Prefer authenticated feed pagination when available; it's far more stable.
        if IG_SESSIONID:
            payload = _fetch_user_feed_page_auth(str(user_id), cursor, count=page_size)
            items = payload.get("items") or []
            if not isinstance(items, list) or not items:
                break
            for item in items:
                if len(collected) >= limit:
                    break
                if not isinstance(item, dict):
                    continue
                collected.append(_item_to_post(item))

            more_available = bool(payload.get("more_available", False))
            next_cursor = payload.get("next_max_id") or payload.get("max_id") or ""
            if not next_cursor or next_cursor == cursor:
                cursor = next_cursor or cursor
                break
            cursor = next_cursor
            continue

        payload = _fetch_graphql_timeline_page(str(user_id), cursor, first=page_size, username=username)

        data = payload.get("data", {})
        user_node = (data.get("user") if isinstance(data, dict) else None) or {}
        media = user_node.get("edge_owner_to_timeline_media", {}) or {}
        edges = media.get("edges", []) or []

        if not isinstance(edges, list) or not edges:
            break

        for edge in edges:
            if len(collected) >= limit:
                break
            node = edge.get("node") if isinstance(edge, dict) else None
            if not isinstance(node, dict):
                continue
            collected.append(_graphql_node_to_post(node))

        page_info = media.get("page_info", {}) or {}
        more_available = bool(page_info.get("has_next_page", False))
        next_cursor = page_info.get("end_cursor") or ""

        # If Instagram doesn't give us a new cursor, stop to avoid loops.
        if not next_cursor or next_cursor == cursor:
            cursor = next_cursor or cursor
            break

        cursor = next_cursor

    return {
        "username": username,
        "returned": len(collected),
        "posts": collected,
        "next_max_id": cursor or "",
        "more_available": bool(more_available),
        "pages_fetched": pages,
        "auth_used": bool(IG_SESSIONID),
    }


@mcp.tool()
def get_post_images(username: str, max_images: int = 6) -> dict:
    """Get just the image URLs from recent posts (skips videos).
    Returns a flat list of HD image URLs.
    Accepts a username or full Instagram URL."""
    username = _clean_username(username)
    user = _fetch_user_data(username)
    media = user.get("edge_owner_to_timeline_media", {})
    edges = media.get("edges", [])

    images = []
    for edge in edges:
        if len(images) >= max_images:
            break
        node = edge["node"]

        # Carousel: get all images
        sidecar = node.get("edge_sidecar_to_children", {}).get("edges", [])
        if sidecar:
            for child in sidecar:
                if len(images) >= max_images:
                    break
                if not child["node"].get("is_video", False):
                    images.append(child["node"].get("display_url", ""))
        elif not node.get("is_video", False):
            images.append(node.get("display_url", ""))

    return {
        "username": username,
        "count": len(images),
        "images": images,
    }


def run_server():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    run_server()
