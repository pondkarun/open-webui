"""Tests for Hermes SSE named-event handling in the chat middleware.

Covers the fix for: named SSE events (``event: hermes.tool.progress``) were
silently skipped by the data-only stream parser, so users saw no tool
activity during long tool-calling turns.

Run: python -m pytest test_hermes_tool_progress.py -v
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parent / 'backend'))


class FakeResponse:
    """Emits the same wire format the Hermes API server produces."""

    def __init__(self, lines):
        self._lines = [l.encode() for l in lines]

    class _Iter:
        def __init__(self, lines):
            self._lines = lines
            self._i = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._i >= len(self._lines):
                raise StopAsyncIteration
            v = self._lines[self._i]
            self._i += 1
            return v

    @property
    def body_iterator(self):
        return self._Iter(self._lines)


def _make_emitted_collector():
    events = []
    em = AsyncMock()
    em.side_effect = lambda ev: events.append(ev)
    return em, events


def test_named_event_lines_are_parsed_and_emitted_as_status():
    """event:+data: pairs must become OWUI status events, not be dropped.

    The middleware module needs heavy deps (torch etc.) so we assert on the
    patched source (regression guard) and behaviorally re-play the exact
    line-classification logic the loop performs.
    """
    lines = [
        'event: hermes.tool.progress',
        'data: {"tool":"terminal","emoji":"💻","label":"pwd","toolCallId":"tc1","status":"running"}',
        '',
        'event: hermes.tool.progress',
        'data: {"tool":"terminal","toolCallId":"tc1","status":"completed"}',
        '',
        'data: {"choices":[{"delta":{"content":"hello"}}]}',
    ]

    # Directly test the parsing loop fragment we added by simulating the
    # middleware's inner loop through a minimal extraction. Since the loop
    # lives inside process_chat_response, we instead assert on the source
    # containing the branch (regression guard) AND behaviorally through the
    # SSE line classification below.
    src = Path('backend/open_webui/utils/middleware.py').read_text()
    assert "pending_sse_event == 'hermes.tool.progress'" in src
    assert "data.startswith('event:')" in src

    # Behavioral: classify lines exactly like the patched loop does
    pending = None
    statuses = []
    for line in lines:
        if line.startswith('event:'):
            pending = line[6:].strip()
            continue
        if not line.startswith('data:'):
            continue
        payload = line[5:].strip()
        if pending == 'hermes.tool.progress':
            import json

            d = json.loads(payload)
            statuses.append((d.get('status'), d.get('label', d.get('tool'))))
            pending = None
    assert statuses == [('running', 'pwd'), ('completed', 'terminal')]


if __name__ == '__main__':
    test_named_event_lines_are_parsed_and_emitted_as_status()
    print('PASS')
