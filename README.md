# TOM — Personal AI Agent

A local-first AI agent that runs on your own machine, talks to any model you want, and gets to know you the more you use it.

## What it is

TOM is a desktop AI assistant designed to be your long-term companion rather than a stateless chatbot. It plugs into both local models (Ollama and friends) and cloud providers (OpenAI, Anthropic, Google, etc.), so you decide where your conversations go.

The core idea is simple: the more you chat with TOM, the better it understands you. It remembers your preferences, recurring projects, and working style, and uses that context to give you more relevant answers over time.

## Features

- **Local-first** — runs entirely on your machine; no data leaves unless you opt in to a cloud provider.
- **Multi-provider** — Ollama, OpenAI, Anthropic, Google, and any other OpenAI-compatible endpoint.
- **MCP support** — plug in Model Context Protocol servers for tools, data sources, and integrations.
- **Skills & Plugins** — extend the agent with custom skills for working with documents (DOCX, PDF), code, and more.
- **Persistent memory** — TOM builds a structured understanding of you and your work over time, and uses it to improve future interactions.
- **Desktop sessions** — a native desktop app with session management, similar to ChatGPT / Claude / Gemini desktop apps.
- **Self-improving** — TOM can analyze past interactions, take notes, refine its skills, and adapt to your patterns.

## Goals

1. A fully open, self-hostable AI agent with no vendor lock-in.
2. A memory system that genuinely compounds in usefulness the longer you use it.
3. A growing library of built-in skills powered by MCP.
4. A clean, fast desktop experience.

## Status

Early stage. See `LICENSE` for licensing (MIT).
