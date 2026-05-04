# Boot stub: pre-load display_manager before _main.mpy fragments heap
import gc
gc.collect()
from ui.display_manager import DisplayManager
gc.collect()
import _main
import asyncio
try:
    asyncio.run(_main.app_main())
finally:
    asyncio.new_event_loop()
