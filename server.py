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
from mcp.server.fastmcp import FastMCP
from curl_cffi import requests

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
        headers=IG_HEADERS,
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
