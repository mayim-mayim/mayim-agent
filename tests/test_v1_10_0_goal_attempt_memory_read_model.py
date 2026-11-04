import asyncio
import json
from urllib.parse import parse_qs, urlsplit
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import requests

from mayim_agent.client import MagClient
from mayim_agent.exceptions import PaymentExecutionError
from mayim_agent.models import AssetType
