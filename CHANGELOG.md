# Changelog

## v1.0.0 (2026-07-12)

### Added
- Complete rewrite using browser automation (agent-browser)
- Image generation via `generate_image_new()`
- Chat via `prompt()` with Instant and Thinking modes
- Conversation management (list, new)
- Media fetching via HTTP GraphQL (`fetch_card_media()`)
- DGW WebSocket frame replay (`replay_frame()`)
- FastAPI REST API server with all endpoints
- Docker + docker-compose deployment
- API key authentication
- Swagger UI docs at `/docs`
- Full exception hierarchy
- Environment variable support for all credentials

### Changed
- Migrated from HTTP GraphQL to browser automation (Meta removed the old GraphQL API)
- Package renamed to `metaai_api` (import as `from metaai_api import MetaAI`)
- PyPI package name: `metaai-sdk`

### Removed
- Old HTTP-based image generation (Meta's GraphQL schema changed)
- `sendMessageStream` subscription (removed from Meta's schema)
- `rewriteOptions`, `AttachmentInput`, `MentionInput` fields (removed by Meta)

### Known Limitations
- At v1.0.0, video generation was not verified as available. In a later live check on 2026-11-02, Meta AI declined video generation for the tested account; see the current account-specific status in [README.md](README.md#current-verification-status).
- Browser method takes ~30-40s per image
- `ecto_1_sess` cookie expires frequently
