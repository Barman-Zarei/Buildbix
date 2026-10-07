import logging
import signal

from ..core.db import init_db
from .runner import run_forever

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    init_db()
    stopping = {"v": False}
    signal.signal(signal.SIGTERM, lambda *_: stopping.update(v=True))
    signal.signal(signal.SIGINT, lambda *_: stopping.update(v=True))
    logging.info("BUILDBIX worker started")
    run_forever(stop=lambda: stopping["v"])
