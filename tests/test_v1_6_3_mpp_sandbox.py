import asyncio
import hashlib
import json
from unittest.mock import patch

import pytest
from mayim_agent.client import MagClient
from mayim_agent.models import ExecutionResult, SettlementReceipt, AttestationSource
