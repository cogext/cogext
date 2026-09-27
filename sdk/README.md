# cogext SDK

Track commitments made by your AI agents — automatically.

## Install

```bash
pip install cogext
```

## 30-second quickstart

```python
from cogext import track, CogextClient
import os

class MyAgent:
    def run(self, prompt: str) -> str:
        return "I will send the report by Tuesday EOD"

agent = MyAgent()
tracked = track(
    agent,
    api_key=os.environ["COGEXT_API_KEY"],
    user_id=os.environ["COGEXT_USER_ID"],
    agent_id="my-agent-001",
)

# Any output from .run() that contains a commitment is automatically tracked
result = tracked.run("Summarise our meeting")
print(result)  # agent output, unmodified

# Fetch open commitments
import asyncio
client = CogextClient(
    api_key=os.environ["COGEXT_API_KEY"],
    user_id=os.environ["COGEXT_USER_ID"],
)
commitments = asyncio.run(client.get_commitments())
print(commitments)
```

## Environment variables

| Variable | Description |
|---|---|
| `COGEXT_API_KEY` | Your API key (any non-empty string in v1) |
| `COGEXT_USER_ID` | UUID that identifies the end user |
| `COGEXT_BASE_URL` | Override the API base URL (default: `https://cogext.onrender.com/api/v1`) |

## Supported agent methods

`track()` intercepts: `.run()`, `.invoke()`, `.chat()`, `.complete()`, and `__call__`.
Works with sync and async methods. Never raises — errors are logged as warnings.

## Links

- Homepage: https://cogextai.com
- Documentation: https://docs.cogextai.com
- Source: https://github.com/cogext/cogext
- Issues: https://github.com/cogext/cogext/issues

## License

Proprietary. Copyright (c) 2026 Yamin / THRYVIX. All rights reserved.

The COGEXT SDK is part of the COGEXT commercial product and is not open source.
For commercial licensing, contact hello@cogextai.com
