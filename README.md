# mcp-instagram

An MCP (Model Context Protocol) server that fetches public Instagram profiles, posts, and images. No API key needed — uses Instagram's mobile API with TLS fingerprint bypass via `curl_cffi`.

## Tools

| Tool | Description |
|------|-------------|
| `get_profile` | Profile info: bio, followers, profile pic (HD), category, verification status |
| `get_posts` | Recent posts with captions, likes, comments, dates, and full carousel support |
| `get_posts_paged` | Recent posts **with pagination** (beyond the ~12 returned by profile preview). Returns `next_max_id` cursor |
| `get_post_images` | Flat list of HD image URLs from recent posts (skips videos) |

All tools accept a **username** (`natgeo`), an **@handle** (`@natgeo`), or a **full URL** (`https://instagram.com/natgeo`).

## Quick Start

### Install

```bash
pip install mcp curl-cffi
```

Or with uv:

```bash
uv pip install mcp curl-cffi
```

### Run standalone

```bash
python server.py
```

The server communicates over **stdio** using the MCP protocol.

### Configure in Claude Code

Add to your `claude_desktop_config.json` or `.claude.json`:

```json
{
  "mcpServers": {
    "instagram": {
      "type": "stdio",
      "command": "python3",
      "args": ["/path/to/mcp-instagram/server.py"]
    }
  }
}
```

Then use in Claude Code:

```
> Get the profile of @natgeo
> Show me the latest posts from nike
> Get images from https://instagram.com/foodnetwork
```

## Example Output

### `get_profile("natgeo")`

```json
{
  "username": "natgeo",
  "full_name": "National Geographic",
  "biography": "Experience the world through the eyes of National Geographic photographers.",
  "profile_pic_url_hd": "https://...",
  "followers": 283000000,
  "is_verified": true,
  "category": "Media/News Company"
}
```

### `get_post_images("natgeo", max_images=3)`

```json
{
  "username": "natgeo",
  "count": 3,
  "images": [
    "https://scontent-...",
    "https://scontent-...",
    "https://scontent-..."
  ]
}
```

### `get_posts_paged("natgeo", limit=36)`

```json
{
  "username": "natgeo",
  "returned": 36,
  "next_max_id": "QVFD...",
  "more_available": true,
  "pages_fetched": 3,
  "posts": [
    { "id": "C0KMCH4vzHJ", "caption": "...", "likes": 123, "comments": 4, "date": "2026-01-01 12:34" }
  ]
}
```

## How It Works

Uses Instagram's **mobile API** (`i.instagram.com/api/v1/`) with an Android user-agent and `curl_cffi` to match Chrome's TLS fingerprint. This avoids the browser-based rate limiting and fingerprint detection that blocks standard HTTP clients.

- No authentication required
- No API key needed
- Works with public profiles only
- Rate limited by Instagram (be respectful)

## Dependencies

- **[mcp](https://pypi.org/project/mcp/)** >= 1.0.0 — Model Context Protocol SDK
- **[curl_cffi](https://pypi.org/project/curl-cffi/)** >= 0.7.0 — HTTP client with TLS fingerprint impersonation

## License

MIT
