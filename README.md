# Launchy

```python
from launchy import Launchy
import asyncio

launchy = Launchy("ls -l --color")

loop = asyncio.get_event_loop()
Launchy.attach_loop(loop)

async def main():
    # start subprocess
    await launchy.launch()

    # wait until subprocess exits
    await launchy.wait()

    # terminate
    await Launchy.stop()

loop.run_until_complete(main())
```
