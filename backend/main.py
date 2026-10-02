import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import router
from api.ws import ConnectionManager
from config import get_settings
from db.session import init_db
from jobs.runner import build_runtime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    manager = ConnectionManager()
    manager.bind_loop(asyncio.get_running_loop())
    app.state.manager = manager
    app.state.runtime = build_runtime(emit=manager.publish)
    yield
    app.state.runtime.runner.shutdown()


app = FastAPI(title="clawback", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
