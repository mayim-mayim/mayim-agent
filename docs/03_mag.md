# mAG adapter and retained rituals

`MagClient` is the service adapter for https://kari.mayim-mayim.com. It inherits
the generic `Payment402Client` policy, payment and verification behavior.

```python
import os
from mayim_agent import MagClient, AssetType

client = MagClient(private_key=os.environ["AGENT_PRIVATE_KEY"])
# Explicitly authorized ritual operations retain their names and contracts.
result = client.draw_omikuji(asset=AssetType.SATS)
```

The async equivalents, Confession, Hono and identity retain their contracts.
Existing scoped Grants are retained only for the ritual exception, not mAG Graph
acquisition. Task clients remain separate from this payment adapter.
Explicit External observations and reporter verification remain available.
No independent Benchmark, Metrics, Sandbox, domain-slot/sponsor/track, Goal,
surface-preflight, trace or old Graph wrapper is provided by the 2.0 adapter.
See [migration](migration-2.0.md) for preserving worker state and evaluator policy.
