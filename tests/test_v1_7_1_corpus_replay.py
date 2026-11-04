# tests/test_v1_7_1_corpus_replay.py
import pytest
import asyncio
from unittest.mock import patch, MagicMock
from mayim_agent.client import MagClient

# ★ 本物の requests.Response に似せた安全なモックを作るヘルパー
def _make_mock_res(status_code, json_data, headers=None, url="http://mock/url"):
    res = MagicMock()
    res.status_code = status_code
    res.headers = headers or {}
    res.json.return_value = json_data
    import json
    res.content = json.dumps(json_data).encode('utf-8')
    res.url = url
    return res
