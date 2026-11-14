import mongomock
import pytest

from memorydelta.config import DB_NAME
from memorydelta.embeddings import HashEmbedder
from memorydelta.model import RecordingModel, ScriptedModel
from memorydelta.seed import seed
from memorydelta.store import MemoryStore


@pytest.fixture
def store():
    """Fresh in-memory Mongo per test (deterministic). Same code paths as Atlas except $vectorSearch."""
    s = MemoryStore(mongomock.MongoClient()[DB_NAME], "local", HashEmbedder())
    seed(s, log=lambda *_: None)
    return s


@pytest.fixture
def model():
    return RecordingModel(ScriptedModel())
