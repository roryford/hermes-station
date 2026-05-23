#!/usr/bin/env python3
"""Restore plugin.yaml files omitted by hermes-agent 0.14.0 wheel. Temporary workaround; remove once upstream PRs #27240/#27268 merge."""

import pathlib
import sysconfig

root = pathlib.Path(sysconfig.get_paths()["purelib"]) / "plugins"

MANIFESTS: dict[str, str] = {
    "web/tavily/plugin.yaml": (
        "name: web-tavily\nversion: 1.0.0\n"
        "description: 'Tavily web search + content extraction + crawl. Requires TAVILY_API_KEY.'\n"
        "author: NousResearch\nkind: backend\nprovides_web_providers:\n  - tavily\n"
    ),
    "web/brave_free/plugin.yaml": (
        "name: web-brave-free\nversion: 1.0.0\n"
        "description: 'Brave Search (free tier). Requires BRAVE_SEARCH_API_KEY.'\n"
        "author: NousResearch\nkind: backend\nprovides_web_providers:\n  - brave-free\n"
    ),
    "web/firecrawl/plugin.yaml": (
        "name: web-firecrawl\nversion: 1.0.0\n"
        "description: 'Firecrawl web search + content extraction. Requires FIRECRAWL_API_KEY.'\n"
        "author: NousResearch\nkind: backend\nprovides_web_providers:\n  - firecrawl\n"
    ),
    "web/ddgs/plugin.yaml": (
        "name: web-ddgs\nversion: 1.0.0\n"
        "description: 'DuckDuckGo web search via ddgs. No API key required.'\n"
        "author: NousResearch\nkind: backend\nprovides_web_providers:\n  - ddgs\n"
    ),
    "web/exa/plugin.yaml": (
        "name: web-exa\nversion: 1.0.0\n"
        "description: 'Exa web search and content extraction. Requires EXA_API_KEY.'\n"
        "author: NousResearch\nkind: backend\nprovides_web_providers:\n  - exa\n"
    ),
    "web/parallel/plugin.yaml": (
        "name: web-parallel\nversion: 1.0.0\n"
        "description: 'Parallel.ai web search + extraction. Requires PARALLEL_API_KEY.'\n"
        "author: NousResearch\nkind: backend\nprovides_web_providers:\n  - parallel\n"
    ),
    "web/searxng/plugin.yaml": (
        "name: web-searxng\nversion: 1.0.0\n"
        "description: 'SearXNG self-hosted metasearch. Requires SEARXNG_URL.'\n"
        "author: NousResearch\nkind: backend\nprovides_web_providers:\n  - searxng\n"
    ),
    "image_gen/openai/plugin.yaml": (
        "name: openai\nversion: 1.0.0\n"
        "description: 'OpenAI image generation (gpt-image-2). Requires OPENAI_API_KEY.'\n"
        "author: NousResearch\nkind: backend\nrequires_env:\n  - OPENAI_API_KEY\n"
    ),
    "image_gen/openai-codex/plugin.yaml": (
        "name: openai-codex\nversion: 1.0.0\n"
        "description: 'OpenAI image generation via ChatGPT/Codex OAuth.'\n"
        "author: NousResearch\nkind: backend\n"
    ),
    "image_gen/xai/plugin.yaml": (
        "name: xai\nversion: 1.0.0\n"
        "description: 'xAI image generation (grok-imagine-image). Requires XAI_API_KEY.'\n"
        "author: Julien Talbot\nkind: backend\nrequires_env:\n  - XAI_API_KEY\n"
    ),
}

for rel, content in MANIFESTS.items():
    dest = root / rel
    if dest.parent.is_dir() and not dest.exists():
        dest.write_text(content)
        print(f"restored: {dest}")
